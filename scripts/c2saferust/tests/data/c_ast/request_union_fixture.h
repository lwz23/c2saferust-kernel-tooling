// SPDX-License-Identifier: GPL-2.0-only

typedef unsigned long long sector_t;

struct io_comp_batch {
	unsigned int nr_entries;
};

struct request {
	int cmd_flags;
	union {
		struct {
			sector_t __sector;
			unsigned int __data_len;
		};
		struct {
			void *special_vec;
			unsigned int special_len;
		};
	};
	struct io_comp_batch *batch;
};
