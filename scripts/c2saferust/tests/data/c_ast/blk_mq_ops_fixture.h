// SPDX-License-Identifier: GPL-2.0-only

typedef unsigned int blk_status_t;
typedef unsigned int blk_qc_t;
typedef unsigned long long sector_t;

struct request {
	sector_t __sector;
	unsigned int __data_len;
};

struct io_comp_batch {
	unsigned int nr_entries;
};

struct blk_mq_hw_ctx {
	int queue_num;
};

struct blk_mq_queue_data {
	struct request *rq;
};

enum blk_eh_timer_return {
	BLK_EH_DONE = 0,
	BLK_EH_RESET_TIMER = 1,
};

struct blk_mq_ops {
	blk_status_t (*queue_rq)(struct blk_mq_hw_ctx *hctx, const struct blk_mq_queue_data *bd);
	void (*complete)(struct request *rq);
	enum blk_eh_timer_return (*timeout)(struct request *rq, _Bool reserved);
	int (*poll)(struct blk_mq_hw_ctx *hctx, struct io_comp_batch *iob);
};

static blk_status_t null_queue_rq(struct blk_mq_hw_ctx *hctx, const struct blk_mq_queue_data *bd)
{
	(void)hctx;
	(void)bd;
	return 0;
}

static void null_complete_rq(struct request *rq)
{
	(void)rq;
}

static enum blk_eh_timer_return null_timeout_rq(struct request *rq, _Bool reserved)
{
	(void)rq;
	(void)reserved;
	return BLK_EH_RESET_TIMER;
}

static int null_poll_rq(struct blk_mq_hw_ctx *hctx, struct io_comp_batch *iob)
{
	(void)hctx;
	(void)iob;
	return 0;
}

static const struct blk_mq_ops null_mq_ops = {
	.queue_rq = null_queue_rq,
	.complete = null_complete_rq,
	.timeout = null_timeout_rq,
	.poll = null_poll_rq,
};
