import os
import io
import shutil
import pickle

import numpy as np
import numpy.lib.format as npformat


class OutputPool:
    """Store node outputs to dictionary-like stores.

    The default store is a Python dictionary.

    Notes
    -----
    See the `elfi.store.BatchStore` interface if you wish to implement your own ELFI
    compatible store.

    """

    def __init__(self, outputs=None):
        """

        Depending on the algorithm, some of these values may be reused
        after making some changes to `ElfiModel` thus speeding up the inference 
        significantly. For instance, if all the simulations are stored in Rejection 
        sampling, one can change the summaries and distances without having to rerun
        the simulator.
        
        Parameters
        ----------
        outputs : list, dict, optional
            list of node names which to store or a dictionary with existing stores. The
            stores are created on demand.
            
        Returns
        -------
        instance : OutputPool
        """

        if outputs is None:
            stores = {}
        elif isinstance(outputs, dict):
            stores = outputs
        else:
            stores = dict.fromkeys(outputs)

        self.stores = stores

        # Context information
        self.batch_size = None
        self.seed = None

    @property
    def context_set(self):
        return self.seed is not None and self.batch_size is not None

    def set_context(self, context):
        """Sets the context of the pool for identifying the batch size and seed for which
        these results are computed.
        
        Parameters
        ----------
        context : elfi.ComputationContext

        Returns
        -------
        None
        """
        if self.context_set:
            raise ValueError('Context is already set')

        self.batch_size = context.batch_size
        self.seed = context.seed

    def get_batch(self, batch_index, outputs=None):
        """Returns a batch from the stores of the pool.
        
        Parameters
        ----------
        batch_index : int
        outputs : list
            which outputs to include to the batch

        Returns
        -------
        batch : dict
        """

        outputs = outputs or self.outputs
        batch = dict()
        for output in outputs:
            store = self.stores[output]
            if store is None:
                continue
            if batch_index in store:
                batch[output] = store[batch_index]
        return batch

    def add_batch(self, batch, batch_index):
        """Adds the outputs from the batch to their stores."""
        for node, values in batch.items():
            if node not in self.stores:
                continue
            store = self._get_store_for(node)

            # Do not add again. The output should be the same.
            if batch_index in store:
                continue

            store[batch_index] = values

    def remove_batch(self, batch_index):
        """Removes the batch from all the stores."""
        for store in self.stores.values():
            if batch_index in store:
                del store[batch_index]

    def has_store(self, name):
        return name in self.stores

    def get_store(self, name):
        return self.stores[name]

    def add_store(self, name, store=None):
        """Adds a store object for a node with name `name`.

        Parameters
        ----------
        name : str
        store : dict, BatchStore

        Returns
        -------
        None

        """
        if name in self.stores and self.stores[name] is not None:
            raise ValueError("Store for '{}' already exists".format(name))

        store = store or self._make_store_for(name)
        self[name] = store

    def remove_store(self, name):
        """Removes a store from the pool

        Parameters
        ----------
        name : str
            Store name

        Returns
        -------
        store
            The removed store
        """
        store = self.stores.pop(name)
        return store

    def _get_store_for(self, name):
        if self.stores[name] is None:
            self.stores[name] = self._make_store_for(name)
        return self.stores[name]

    def _make_store_for(self, name):
        """Make a default store for a node

        All the default stores will be created through this method.
        """
        return {}

    def __len__(self):
        """Largest batch index in any of the stores"""
        l = 0
        for output, store in self.stores.items():
            if store is None:
                continue
            l = max(l, len(store))
        return l

    def __getitem__(self, batch_index):
        """Return the batch"""
        return self.get_batch(batch_index)

    def __setitem__(self, batch_index, batch):
        return self.add_batch(batch, batch_index)

    def __contains__(self, batch_index):
        return len(self) > batch_index

    def clear(self):
        """Removes all data from the stores"""
        for store in self.stores.values():
            store.clear()

    @property
    def outputs(self):
        return list(self.stores.keys())


# TODO: Make it easier to load ArrayPool with just a name.
#       we could store the context to the pool folder, and drop the use of a seed in the
#       folder name
class ArrayPool(OutputPool):
    """Store node outputs to .npy arrays.

    The default store for output data is a numpy binary `.npy` file, that stores array
    data. Separate files will be created for different nodes.

    Notes
    -----

    Internally the `elfi.ArrayPool` will create an `elfi.store.BatchArrayStore' object
    wrapping a `NpyPersistedArray` for each output. The `elfi.store.NpyPersistedArray`
    object is responsible for managing the `.npy` file.
    """

    def __init__(self, outputs, name=None, path=None):
        """

        Parameters
        ----------
        outputs : list
            name of nodes whose output to store to a numpy .npy file.
        name : str
            Name of the pool. This will be part of the path where the data are stored.
        path : str
            Path to directory under which `elfi.ArrayPool` will place its folders and
            files. Default is ./pools, where . is the current working directory.
            
        Returns
        -------
        instance : ArrayPool
        """
        super(ArrayPool, self).__init__(outputs)

        if name is not None:
            # TODO: load the pool with this name
            pass
        self.name = name
        self.path = path or self._default_path()
        os.makedirs(self.path, exist_ok=True)

    @property
    def arraypath(self):
        """Path to where the array files are stored.
        
        Returns
        -------
        path : str
        """
        if self.name is None:
            return None
        return self._arraypath(self.name, self.path)

    def _make_store_for(self, name):
        if not self.context_set:
            raise ValueError('Arraypool has no context set')
        if self.name is None:
            self.name = 'arraypool_{}'.format(self.seed)
            os.makedirs(self.arraypath)

        filename = os.path.join(self.arraypath, name)
        array = NpyPersistedArray(filename)
        return BatchArrayStore(array, self.batch_size)

    def delete(self):
        """Removes the folder and all the data in this pool."""
        if self.arraypath is None:
            return

        self.close()
        shutil.rmtree(self.arraypath)

    def close(self):
        """Closes the array files of the stores and store the pool to the disk.

        You can reopen the pool with ArrayPool.open.
        """
        for store in self.stores.values():
            if hasattr(store, 'array') and hasattr(store.array, 'close'):
                store.array.close()
        try:
            filename = os.path.join(self.arraypath, self._pkl_name())
            pickle.dump(self, open(filename, "wb" ) )
        except:
            raise ValueError('Pickling of the pool object failed. Please check that your '
                             'stores and data are pickelable. Arrays were stored to disk '
                             'succesfully.')

    def flush(self):
        """Flushes all array files of the stores."""
        for store in self.stores.values():
            if hasattr(store, 'array') and hasattr(store.array, 'flush'):
                store.array.flush()

    @classmethod
    def open(cls, name, path=None):
        """Open a closed ArrayPool from disk

        Parameters
        ----------
        name : str
        path : str, optional

        Returns
        -------

        """
        path = path or cls._default_path()
        filename = os.path.join(cls._arraypath(name, path), cls._pkl_name())
        return pickle.load(open(filename, "rb" ))

    @classmethod
    def _pkl_name(cls):
        return cls.__name__.lower() + '.pkl'

    @classmethod
    def _arraypath(cls, name, path):
        return os.path.join(path, name)

    @classmethod
    def _default_path(cls):
        return os.path.join(os.getcwd(), 'pools')



class BatchStore:
    """Stores batches for a single node"""
    def __getitem__(self, batch_index):
        raise NotImplementedError

    def __setitem__(self, batch_index, data):
        raise NotImplementedError

    def __delitem__(self, batch_index):
        raise NotImplementedError

    def __contains__(self, batch_index):
        raise NotImplementedError

    def __len__(self):
        """Number of batches in the store"""
        raise NotImplementedError

    def clear(self):
        """Remove all batches from the store"""
        raise NotImplementedError


# TODO: add mask for missing items. It should replace the use of `current_index`.
#       This should make it possible to also append further than directly to the end
#       of current index or length of the array.
class BatchArrayStore(BatchStore):
    """Helper class to use arrays as data stores in ELFI"""
    def __init__(self, array, batch_size, n_batches=0):
        """

        Parameters
        ----------
        array
            Any array like object supporting Python list indexing
        batch_size : int
            Size of a batch of data
        n_batches : int
            When using pre allocated arrays, this keeps track of the number of batches
            currently stored to the array.
        """
        self.array = array
        self.batch_size = batch_size
        self.n_batches = n_batches

    def __contains__(self, batch_index):
        b = self._to_slice(batch_index).stop
        return batch_index < self.n_batches and b <= len(self.array)

    def __getitem__(self, batch_index):
        sl = self._to_slice(batch_index)
        return self.array[sl]

    def __setitem__(self, batch_index, data):
        sl = self._to_slice(batch_index)

        if batch_index in self:
            self.array[sl] = data
        elif batch_index == self.n_batches:
            # Append a new batch
            if sl.stop <= len(self.array):
                self.array[sl] = data
            elif sl.start == len(self.array) and hasattr(self.array, 'append'):
                # NpyPersistedArray supports appending
                self.array.append(data)
            else:
                raise ValueError("There is not enough space in the array")
            self.n_batches += 1
        else:
            raise ValueError("Appending further than the end of the array is not yet "
                             "supported")

    def __delitem__(self, batch_index):
        if batch_index not in self:
            raise IndexError("Cannot remove, batch index {} is not in the array"
                             .format(batch_index))
        elif batch_index != self.n_batches:
            raise IndexError("It is not yet possible to remove batches from the middle "
                             "of the array")

        if hasattr(self.array, 'truncate'):
            sl = self._to_slice(batch_index)
            self.array.truncate(sl.start)

        self.n_batches -= 1

    def __len__(self):
        return int(len(self.array)/self.batch_size)

    def _to_slice(self, batch_index):
        a = self.batch_size*batch_index
        return slice(a, a + self.batch_size)

    def clear(self):
        if hasattr(self.array, 'clear'):
            self.array.clear()
        self.n_batches = 0


class NpyPersistedArray:
    """

    Notes
    -----
    - Supports only binary files.
    - Supports only .npy version 2.0
    - See numpy.lib.npformat for documentation of the .npy format """

    MAX_SHAPE_LEN = 2**64

    # Version 2.0 header prefix length
    HEADER_DATA_OFFSET = 12
    HEADER_DATA_SIZE_OFFSET = 8

    def __init__(self, name, array=None, truncate=False):
        """

        Parameters
        ----------
        name : str
            File name
        array : ndarray, optional
            Initial array
        truncate : bool
            Whether to truncate the file or not
        """

        self.header_length = None
        self.itemsize = None

        # Header data fields
        self.shape = None
        self.fortran_order = False
        self.dtype = None

        # The header bytes must be prepared in advance, because there is an import in
        # `numpy.lib.format._write_array_header` (1.11.3) that fails if the program is
        # being closed on exception and would corrupt the .npy file.
        self._header_bytes_to_write = None

        if name[-4:] != '.npy':
            name += '.npy'
        self.name = name

        self.fs = None
        if truncate is False and os.path.exists(self.name):
            self.fs = open(self.name, 'r+b')
            self._init_from_file_header()
        else:
            self.fs = open(self.name, 'w+b')

        if array:
            self.append(array)
            self.flush()

    def __getitem__(self, sl):
        if self.header_length is None:
            raise IndexError()
        order = 'F' if self.fortran_order else 'C'
        # TODO: do not recreate if nothing has changed
        mmap = np.memmap(self.fs, dtype=self.dtype, shape=self.shape,
                         offset=self.header_length, order=order)
        return mmap[sl]

    def __setitem__(self, sl, value):
        if self.header_length is None:
            raise IndexError()
        order = 'F' if self.fortran_order else 'C'
        mmap = np.memmap(self.fs, dtype=self.dtype, shape=self.shape,
                         offset=self.header_length, order=order)
        mmap[sl] = value

    def __len__(self):
        return self.shape[0] if self.shape else 0

    @property
    def size(self):
        return np.prod(self.shape)

    def append(self, array):
        """Append data from array to self."""
        if self.closed:
            raise ValueError('Array is not opened.')

        if not self.initialized:
            self._init_from_array(array)

        if array.shape[1:] != self.shape[1:]:
            raise ValueError("Appended array is of different shape")
        elif array.dtype != self.dtype:
            raise ValueError("Appended array is of different dtype")

        # Append new data
        self.fs.seek(0, 2)
        self.fs.write(array.tobytes('C'))
        self.shape = (self.shape[0] + len(array),) + self.shape[1:]

        # Only prepare the header bytes, need to be flushed to take effect
        self._prepare_header_data()

    def _init_from_file_header(self):
        """Initialize the object from existing file"""
        self.fs.seek(self.HEADER_DATA_SIZE_OFFSET)
        self.shape, fortran_order, self.dtype = npformat.read_array_header_2_0(
            self.fs)
        self.header_length = self.fs.tell()

        if fortran_order:
            raise ValueError('Column major (Fortran-style) files are not supported. Please'
                             'translate if first to row major (C-style).')

        # Determine itemsize
        shape = (0,) + self.shape[1:]
        self.itemsize = np.empty(shape=shape, dtype=self.dtype).itemsize

    def _init_from_array(self, array):
        """Initialize the object from an array.

        Sets the the header_length so large that it is possible to append to the array.

        Returns
        -------
        h_bytes : io.BytesIO
            Contains the oversized header bytes

        """
        self.shape = (0,) + array.shape[1:]
        self.dtype = array.dtype
        self.itemsize = array.itemsize

        # Read header data from array and set modify it to be large for the length
        # 1_0 is the same for 2_0
        d = npformat.header_data_from_array_1_0(array)
        d['shape'] = (self.MAX_SHAPE_LEN,) + d['shape'][1:]
        d['fortran_order'] = False

        # Write a prefix for a very long array to make it large enough for appending new
        # data
        h_bytes = io.BytesIO()
        npformat.write_array_header_2_0(h_bytes, d)
        self.header_length = h_bytes.tell()

        # Write header prefix to file
        self.fs.seek(0)
        h_bytes.seek(0)
        self.fs.write(h_bytes.read(self.HEADER_DATA_OFFSET))

        # Write header data for the zero length to make it a valid file
        self._prepare_header_data()
        self._write_header_data()

    def truncate(self, length=0):
        """Truncates the array to the specified length

        Parameters
        ----------
        length : int
            Length (=`shape[0]`) of the array to truncate to. Default 0.

        Returns
        -------

        """
        if self.fs is None:
            raise ValueError('Array has been deleted')
        elif self.fs.closed:
            raise ValueError('Array has been closed')

        # Reset length
        self.shape = (length,) + self.shape[1:]
        self._prepare_header_data()
        self._write_header_data()

        self.fs.seek(self.header_length + self.size*self.itemsize)
        self.fs.truncate()

    def close(self):
        if self.initialized:
            self._write_header_data()
            self.fs.close()

    def clear(self):
        self.truncate(0)

    def delete(self):
        """Removes the file and invalidates this array"""
        if self.deleted:
            return
        name = self.fs.name
        self.close()
        os.remove(name)
        self.fs = None
        self.header_length = None

    def flush(self):
        self._write_header_data()
        self.fs.flush()

    def __del__(self):
        self.close()

    def _prepare_header_data(self):
        # Make header data
        d = {
            'shape': self.shape,
            'fortran_order': self.fortran_order,
            'descr': npformat.dtype_to_descr(self.dtype)
        }

        h_bytes = io.BytesIO()
        npformat.write_array_header_2_0(h_bytes, d)

        # Pad the end of the header
        fill_len = self.header_length - h_bytes.tell()
        if fill_len < 0:
            raise OverflowError("File {} cannot be appended. The header is too short.".
                                format(self.name))
        elif fill_len > 0:
            h_bytes.write(b'\x20' * fill_len)

        h_bytes.seek(0)
        self._header_bytes_to_write = h_bytes.read()

    def _write_header_data(self):
        if not self._header_bytes_to_write:
            return

        # Rewrite header data
        self.fs.seek(self.HEADER_DATA_OFFSET)
        h_bytes = self._header_bytes_to_write[self.HEADER_DATA_OFFSET:]
        self.fs.write(h_bytes)

        # Flag bytes off as they are now written
        self._header_bytes_to_write = None

    @property
    def deleted(self):
        return self.fs is None

    @property
    def closed(self):
        return self.deleted or self.fs.closed

    @property
    def initialized(self):
        return (not self.closed) and (self.header_length is not None)

    def __getstate__(self):
        if not self.fs.closed:
            self.flush()
        return {'name': self.name}

    def __setstate__(self, state):
        name = state.pop('name')
        self.__init__(name)

