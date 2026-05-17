import cffi

# Initialize CFFI
ffi = cffi.FFI()

# Define the C interface for t1ha0
ffi.cdef("""
uint64_t t1ha0(const void *data, size_t len, uint64_t seed);
""")

# Compile the module
ffi.set_source("_t1ha0_module",
    """
    #include "t1ha.h"
    """,
    sources=["t1ha0.c","t1ha1.c"],  # Path to t1ha0.c and t1ha1.c source file
    include_dirs=["."],   # Directory containing t1ha.h
)

# Compile only if run as main (out-of-line mode)
if __name__ == "__main__":
    ffi.compile(verbose=True)