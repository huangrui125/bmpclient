"""
bmpclient/umm_client.py — ctypes 绑定，封装 UMM C API。

加载 UMM 的 libumm.so，提供 Pythonic 的 Chunk 分配 / 释放 / 读写接口。
"""

import ctypes
import os
import sys

# ========================================================================
# C structure definitions (must match include/umm.h)
# ========================================================================


class ChunkDescriptor(ctypes.Structure):
    _fields_ = [
        ("chunk_id", ctypes.c_uint64),
        ("base_gpa", ctypes.c_uint64),
        ("user_size", ctypes.c_uint64),
    ]

    def __repr__(self):
        return f"ChunkDescriptor(chunk_id={self.chunk_id}, base_gpa=0x{self.base_gpa:016x}, user_size={self.user_size})"


class ChunkMetadata(ctypes.Structure):
    _fields_ = [
        ("chunk_id", ctypes.c_uint64),
        ("name", ctypes.c_char * 64),
        ("gpa", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("primary_tier", ctypes.c_uint8),
        ("has_ssd_copy", ctypes.c_uint8),
    ]


class SsdDeviceConfig(ctypes.Structure):
    _fields_ = [
        ("path", ctypes.c_char * 256),
        ("size", ctypes.c_uint64),
    ]


class StorageResource(ctypes.Structure):
    _fields_ = [
        ("tier", ctypes.c_uint8),
        ("device_path", ctypes.c_char * 256),
        ("capacity", ctypes.c_uint64),
        ("base_offset", ctypes.c_uint64),
        ("online", ctypes.c_int),
    ]


UMM_MAX_SSD_DEVICES = 16
UMM_MAX_TOPOLOGY_RESOURCES = 4 + UMM_MAX_SSD_DEVICES


class StorageTopology(ctypes.Structure):
    _fields_ = [
        ("node_id", ctypes.c_uint8),
        ("num_resources", ctypes.c_uint32),
        ("resources", StorageResource * UMM_MAX_TOPOLOGY_RESOURCES),
    ]


class UMMConfig(ctypes.Structure):
    _fields_ = [
        ("transport", ctypes.c_char * 16),
        ("consistency_model", ctypes.c_char * 16),
        ("memory_size", ctypes.c_uint64),
        ("meta_server_addr", ctypes.c_char * 256),
        ("mem_server_addr", ctypes.c_char * 256),
        ("cxl_device", ctypes.c_char * 256),
        ("ssd_device", ctypes.c_char * 256),
        ("my_node_id", ctypes.c_uint8),
        ("ssd_devices", SsdDeviceConfig * 16),
        ("num_ssd_devices", ctypes.c_uint32),
        ("listen_port", ctypes.c_uint16),
        ("base_gpa", ctypes.c_uint64),
    ]


# Tier IDs (match include/umm.h)
UMM_TIER_DRAM = 0
UMM_TIER_CXL = 1
UMM_TIER_SSD = 2
UMM_TIER_RESV = 3

UMM_OK = 0


def find_libumm_so() -> str:
    """
    按优先级查找 libumm.so：
      1. UMM_BUILD_DIR 环境变量
      2. ../UMM/build/libumm.so
      3. ../../UMM/build/libumm.so
    """
    candidates = []
    env = os.environ.get("UMM_BUILD_DIR")
    if env:
        candidates.append(os.path.join(env, "libumm.so"))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "..", "UMM", "build", "libumm.so"))
    candidates.append(os.path.join(script_dir, "..", "..", "UMM", "build", "libumm.so"))

    for path in candidates:
        abs_path = os.path.abspath(path)
        if os.path.isfile(abs_path):
            return abs_path

    raise FileNotFoundError(
        f"libumm.so not found. Searched: {candidates}\n"
        "Please build UMM with: cd UMM && make shared"
    )


class UMMLib:
    """封装 libumm.so 的 ctypes 调用。"""

    def __init__(self, so_path: str = None):
        if so_path is None:
            so_path = find_libumm_so()
        self._lib = ctypes.CDLL(so_path)
        self._setup_signatures()

    def _setup_signatures(self):
        lib = self._lib

        lib.umm_init.argtypes = [ctypes.POINTER(UMMConfig)]
        lib.umm_init.restype = ctypes.c_int

        lib.umm_deinit.argtypes = []
        lib.umm_deinit.restype = None

        lib.umm_alloc.argtypes = [ctypes.c_uint64, ctypes.POINTER(ChunkDescriptor)]
        lib.umm_alloc.restype = ctypes.c_int

        lib.umm_alloc_tiered.argtypes = [
            ctypes.c_uint64,
            ctypes.c_uint8,
            ctypes.POINTER(ChunkDescriptor),
        ]
        lib.umm_alloc_tiered.restype = ctypes.c_int

        lib.umm_alloc_on_device.argtypes = [
            ctypes.c_uint64,
            ctypes.c_uint8,
            ctypes.c_uint32,
            ctypes.POINTER(ChunkDescriptor),
        ]
        lib.umm_alloc_on_device.restype = ctypes.c_int

        lib.umm_free.argtypes = [ctypes.POINTER(ChunkDescriptor)]
        lib.umm_free.restype = ctypes.c_int

        lib.umm_read.argtypes = [
            ctypes.POINTER(ChunkDescriptor),
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_void_p,
        ]
        lib.umm_read.restype = ctypes.c_int

        lib.umm_write.argtypes = [
            ctypes.POINTER(ChunkDescriptor),
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_void_p,
        ]
        lib.umm_write.restype = ctypes.c_int

        lib.umm_lookup_chunk.argtypes = [ctypes.c_char_p, ctypes.POINTER(ChunkMetadata)]
        lib.umm_lookup_chunk.restype = ctypes.c_int

        lib.umm_error_string.argtypes = [ctypes.c_int]
        lib.umm_error_string.restype = ctypes.c_char_p

        lib.umm_tier_name.argtypes = [ctypes.c_uint8]
        lib.umm_tier_name.restype = ctypes.c_char_p

        lib.umm_get_topology.argtypes = [ctypes.POINTER(StorageTopology)]
        lib.umm_get_topology.restype = ctypes.c_int

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def init(self, cfg: UMMConfig) -> int:
        return self._lib.umm_init(ctypes.byref(cfg))

    def deinit(self) -> None:
        self._lib.umm_deinit()

    def alloc(self, size: int) -> ChunkDescriptor:
        desc = ChunkDescriptor()
        rc = self._lib.umm_alloc(size, ctypes.byref(desc))
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_alloc({size}) failed: rc={rc} ({self.errstr(rc)})"
            )
        return desc

    def alloc_tiered(self, size: int, tier: int) -> ChunkDescriptor:
        desc = ChunkDescriptor()
        rc = self._lib.umm_alloc_tiered(size, tier, ctypes.byref(desc))
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_alloc_tiered({size}, tier={tier}) failed: rc={rc} ({self.errstr(rc)})"
            )
        return desc

    def alloc_on_device(self, size: int, tier: int, device_idx: int) -> ChunkDescriptor:
        desc = ChunkDescriptor()
        rc = self._lib.umm_alloc_on_device(size, tier, device_idx, ctypes.byref(desc))
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_alloc_on_device({size}, tier={tier}, device={device_idx}) "
                f"failed: rc={rc} ({self.errstr(rc)})"
            )
        return desc

    def get_topology(self) -> StorageTopology:
        topo = StorageTopology()
        rc = self._lib.umm_get_topology(ctypes.byref(topo))
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_get_topology failed: rc={rc} ({self.errstr(rc)})"
            )
        return topo

    def free(self, desc: ChunkDescriptor) -> None:
        rc = self._lib.umm_free(ctypes.byref(desc))
        if rc != UMM_OK:
            raise RuntimeError(f"umm_free failed: rc={rc} ({self.errstr(rc)})")

    def read(self, desc: ChunkDescriptor, offset: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        rc = self._lib.umm_read(
            ctypes.byref(desc), offset, size, ctypes.cast(buf, ctypes.c_void_p)
        )
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_read failed: rc={rc} ({self.errstr(rc)})"
            )
        return buf.raw

    def write(self, desc: ChunkDescriptor, offset: int, data: bytes) -> None:
        if not data:
            return
        buf = ctypes.create_string_buffer(data)
        rc = self._lib.umm_write(
            ctypes.byref(desc), offset, len(data), ctypes.cast(buf, ctypes.c_void_p)
        )
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_write failed: rc={rc} ({self.errstr(rc)})"
            )

    def lookup_chunk(self, name: str) -> ChunkMetadata:
        meta = ChunkMetadata()
        rc = self._lib.umm_lookup_chunk(name.encode("utf-8"), ctypes.byref(meta))
        if rc != UMM_OK:
            raise RuntimeError(
                f"umm_lookup_chunk('{name}') failed: rc={rc} ({self.errstr(rc)})"
            )
        return meta

    def errstr(self, rc: int) -> str:
        s = self._lib.umm_error_string(rc)
        return s.decode("utf-8") if s else f"unknown({rc})"

    def tier_name(self, tier: int) -> str:
        s = self._lib.umm_tier_name(tier)
        return s.decode("utf-8") if s else f"tier_{tier}"
