import numpy as np
from numbers import Number
from itertools import chain
from .raggedshape import RaggedShape

HANDLED_FUNCTIONS = {}

class RaggedArray(np.lib.mixins.NDArrayOperatorsMixin):
    def __init__(self, data, offsets=None, shape=None, dtype=None):
        if offsets is None and shape is None:
            data, offsets = self.from_array_list(data, dtype)
            shape = RaggedShape(offsets)
        elif shape is not None:
            offsets = shape._offsets
        else:
            shape = RaggedShape(offsets)
        
        # offsets = np.asanyarray(offsets, dtype=np.int32)
        self.shape = shape
        self._data = np.asanyarray(data)
        self._offsets = offsets

        self._row_starts = self._offsets[:-1]
        self._row_ends = self._offsets[1:]
        self._row_lens = (self._row_ends-self._row_starts).astype(np.int16)

    @property
    def size(self):
        return self._data.size

    def __len__(self):
        return len(self._row_starts)

    def save(self, filename):
        np.savez(filename, data=self._data, offsets=self._offsets)

    @classmethod
    def load(cls, filename):
        D = np.load(filename)
        offsets = np.asanyarray(D["offsets"], dtype=np.int32)
        return cls(D["data"], offsets)

    @property
    def dtype(self):
        return self._data.dtype

    def __iter__(self):
        return (self._data[start:end].tolist() for start, end in zip(self._row_starts, self._row_ends))

    def __repr__(self):
        return f"RaggedArray({repr(self._data)}, {repr(self._offsets)})"

    def __str__(self):
        return str(self.to_array_list())

    def equals(self, other):
        return np.all(self._data==other._data) and np.all(self._offsets == other._offsets)

    def to_array_list(self):
        return list(self) # [self._data[start:end] for start, end in zip(self._row_starts, self._row_ends)]

    @classmethod
    def from_array_list(cls, array_list, dtype=None):
        offsets = np.cumsum([0] + [len(array) for array in array_list], dtype=np.int32)
        data_size = offsets[-1]
        data = np.array([element for array in array_list for element in array], dtype=dtype) # This can be done faster
        return data, offsets

    def get_row_lengths(self):
        return self._row_lens# self._row_ends-self._row_starts

    ########### Indexing
    def __getitem__(self, index):
        if isinstance(index, tuple):
            assert len(index)==2
            return self._get_element(index[0], index[1])
        elif isinstance(index, Number):
            return self._get_row(index)
        elif isinstance(index, slice):
            assert (index.step is None) or index.step==1
            return self._get_rows(index.start, index.stop)
        elif isinstance(index, np.ndarray) and index.dtype==bool:
            return self._get_rows_from_boolean(index)
        elif isinstance(index, list) or isinstance(index, np.ndarray):
            return self._get_multiple_rows(index)
        else:
            return NotImplemented


    def _get_row(self, index):
        assert 0 <= index < self._row_starts.size, (0, index, self._row_starts.size)
        return self._data[self._row_starts[index]:self._row_ends[index]]

    def _get_rows(self, from_row, to_row):
        data_start = self._offsets[from_row]
        data_end = self._offsets[to_row]
        new_data = self._data[data_start:data_end]
        new_offsets = self._offsets[from_row:to_row+1]
        return self.__class__(new_data, new_offsets-data_start)

    def _get_rows_from_boolean(self, boolean_array):
        rows = np.flatnonzero(boolean_array)
        return self._get_multiple_rows(rows)

    def _build_indices(self, starts, ends):
        row_lens = ends-starts
        # valid_rows = np.flatnonzero(row_lens)
        offsets = np.insert(np.cumsum(row_lens), 0, 0)
        index_builder = np.ones(row_lens.sum()+1, dtype=int)
        index_builder[offsets[:0:-1]] -= ends[::-1]
        index_builder[0] = 0 
        index_builder[offsets[:-1]] += starts

        #index_builder[offsets[:-1][valid_rows]] += starts[valid_rows]
        # index_builder[offsets[1:][valid_rows]] -= ends[valid_rows]
        indices = np.cumsum(index_builder[:-1])
        return indices, offsets

    def _get_multiple_rows(self, rows):
        starts = self._row_starts[rows]
        ends = self._row_ends[rows]
        indices, new_offsets = self._build_indices(starts, ends)
        new_data = self._data[indices]
        return self.__class__(new_data, new_offsets)

    def _get_element(self, row, col):
        flat_idx = self._row_starts[row] + col
        assert np.all(flat_idx < self._row_ends[row])
        return self._data[flat_idx]

    def _build_mask(self, starts, ends):
        full_boolean_array = np.zeros(self._data.size+1, dtype=bool)
        full_boolean_array[starts] = True
        full_boolean_array[ends] ^= True
        return np.logical_xor.accumulate(full_boolean_array)[:-1]

    ### Broadcasting
    def _broadcast_rows(self, values):
        assert values.shape == (self._row_starts.size, 1)
        values = values.ravel()
        broadcast_builder = np.zeros(self._data.size+1, self._data.dtype)
        broadcast_builder[self._row_ends[::-1]] -= values[::-1]
        broadcast_builder[0] = 0 
        broadcast_builder[self._row_starts] += values
        func = np.logical_xor if values.dtype==bool else np.add
        return self.__class__(func.accumulate(broadcast_builder[:-1]), self._offsets)

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        if method != '__call__':
            return NotImplemented
        
        datas = []
        for input in inputs:
            if isinstance(input, Number):
                datas.append(input)
            elif isinstance(input, np.ndarray):
                broadcasted = self._broadcast_rows(input)
                datas.append(broadcasted._data)
            elif isinstance(input, self.__class__):
                datas.append(input._data)
                if np.any(input._offsets != self._offsets):
                    raise TypeError("inconsistent sizes")
            else:
                return NotImplemented
        return self.__class__(ufunc(*datas, **kwargs), self._offsets)

    def _index_array(self):
        diffs = np.zeros(self._data.size, dtype=int)
        diffs[self._offsets[1:-1]] = 1
        return np.cumsum(diffs)

    def sum(self, axis=None):
        if axis is None:
            return self._data.sum()
        if axis == -1 or axis==1:
            return np.bincount(self._index_array(), self._data, minlength=self._row_starts.size)
        return NotImplemented

    def row_sizes(self):
        return self._row_ends-self._row_starts

    def mean(self, axis=None):
        s = self.sum(axis=axis)
        if axis is None:
            return s/self._data.size
        if axis == -1 or axis==1:
            return s/self.row_sizes()
        return NotImplemented

    def all(self, axis=None):
        if axis is None:
            return np.all(self._data)
        return NotImplemented

    def __array_function__(self, func, types, args, kwargs):
        if func not in HANDLED_FUNCTIONS:
            return NotImplemented
        # Note: this allows subclasses that don't override
        # __array_function__ to handle DiagonalArray objects.
        if not all(issubclass(t, self.__class__) for t in types):
            return NotImplemented
        return HANDLED_FUNCTIONS[func](*args, **kwargs)

    @classmethod
    def concatenate(cls, ragged_arrays):
        data = np.concatenate([ra._data for ra in ragged_arrays])
        row_sizes = np.concatenate([[0]]+[ra.row_sizes() for ra in ragged_arrays])
        offsets = np.cumsum(row_sizes)
        return cls(data, offsets)

    def ravel(self):
        return self._data

    def nonzero(self):
        flat_indices = np.flatnonzero(self._data)
        return self.unravel_multi_index(flat_indices)
        
    def ravel_multi_index(self, indices):
        return self._offsets[indices[0]]+indices[1]

    def unravel_multi_index(self, flat_indices):
        rows = np.searchsorted(self._offsets, flat_indices, side="right")-1
        cols = flat_indices-self._offsets[rows]
        return rows, cols

def implements(np_function):
   "Register an __array_function__ implementation for DiagonalArray objects."
   def decorator(func):
       HANDLED_FUNCTIONS[np_function] = func
       return func
   return decorator

@implements(np.concatenate)
def concatenate(ragged_arrays):
    data = np.concatenate([ra._data for ra in ragged_arrays])
    row_sizes = np.concatenate([[0]]+[ra.row_sizes() for ra in ragged_arrays])
    offsets = np.cumsum(row_sizes)
    return RaggedArray(data, offsets)

@implements(np.all)
def our_all(ragged_array):
    return ragged_array.all()

@implements(np.nonzero)
def nonzero(ragged_array):
    return ragged_array.nonzero()

@implements(np.zeros_like)
def zeros_like(ragged_array, dtype=None, shape=None):
    shape = ragged_array.shape if shape is None else shape
    dtype = ragged_array.dtype if dtype is None else dtype
    data = np.zeros(shape.size, dtype=dtype)
    return RaggedArray(data, shape=shape)
