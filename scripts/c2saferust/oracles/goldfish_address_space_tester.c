// SPDX-License-Identifier: GPL-2.0

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/ioctl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <unistd.h>

#define LOG_PREFIX "[goldfish-oracle]"
#define DEVICE_NODE "/dev/goldfish_address_space"

#define DEVICE_TYPE_HOST_MEMORY_ALLOCATOR_ID 5
#define HOST_MEMORY_ALLOCATOR_COMMAND_ALLOCATE_ID 1
#define HOST_MEMORY_ALLOCATOR_COMMAND_UNALLOCATE_ID 2

#define NOT_RUN (-99999)

struct goldfish_address_space_allocate_block {
    uint64_t size;
    uint64_t offset;
    uint64_t phys_addr;
};

struct goldfish_address_space_ping {
    uint64_t offset;
    uint64_t size;
    uint64_t metadata;
    uint32_t version;
    uint32_t wait_fd;
    uint32_t wait_flags;
    uint32_t direction;
};

struct goldfish_address_space_claim_shared {
    uint64_t offset;
    uint64_t size;
};

#define GOLDFISH_ADDRESS_SPACE_IOCTL_MAGIC 'G'
#define GOLDFISH_ADDRESS_SPACE_IOCTL_OP(OP, T) _IOWR(GOLDFISH_ADDRESS_SPACE_IOCTL_MAGIC, OP, T)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_ALLOCATE_BLOCK \
    GOLDFISH_ADDRESS_SPACE_IOCTL_OP(10, struct goldfish_address_space_allocate_block)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_DEALLOCATE_BLOCK \
    GOLDFISH_ADDRESS_SPACE_IOCTL_OP(11, uint64_t)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_PING \
    GOLDFISH_ADDRESS_SPACE_IOCTL_OP(12, struct goldfish_address_space_ping)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_CLAIM_SHARED \
    GOLDFISH_ADDRESS_SPACE_IOCTL_OP(13, struct goldfish_address_space_claim_shared)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_UNCLAIM_SHARED \
    GOLDFISH_ADDRESS_SPACE_IOCTL_OP(14, uint64_t)
#define GOLDFISH_ADDRESS_SPACE_IOCTL_UNKNOWN \
    _IO(GOLDFISH_ADDRESS_SPACE_IOCTL_MAGIC, 99)

static void log_result(const char *name, long value)
{
    printf("%s %s=%ld\n", LOG_PREFIX, name, value);
}

int main(void)
{
    int fd = -1;
    int second_fd = -1;
    void *mapping = MAP_FAILED;
    long open_rc = NOT_RUN;
    long ping_device_type_rc = NOT_RUN;
    long allocate_block_rc = NOT_RUN;
    long ping_allocate_rc = NOT_RUN;
    long mmap_rc = NOT_RUN;
    long invalid_mmap_rc = NOT_RUN;
    long claim_shared_rc = NOT_RUN;
    long unclaim_shared_rc = NOT_RUN;
    long second_unclaim_shared_rc = NOT_RUN;
    long ping_unallocate_rc = NOT_RUN;
    long deallocate_block_rc = NOT_RUN;
    long deallocate_missing_rc = NOT_RUN;
    long unknown_ioctl_rc = NOT_RUN;
    long close_rc = NOT_RUN;
    long reopen_rc = NOT_RUN;
    long reclose_rc = NOT_RUN;
    long result = 1;
    const size_t requested_size = 4096;
    struct goldfish_address_space_allocate_block request = {0};
    struct goldfish_address_space_ping ping = {0};
    struct goldfish_address_space_claim_shared claim = {0};
    uint64_t deallocate_offset = 0;
    uint64_t unclaim_offset = 0;
    long page_size = sysconf(_SC_PAGESIZE);

    fd = open(DEVICE_NODE, O_RDWR | O_CLOEXEC);
    open_rc = fd >= 0 ? 0 : -errno;
    if (open_rc != 0) {
        goto out;
    }

    memset(&ping, 0, sizeof(ping));
    ping.metadata = DEVICE_TYPE_HOST_MEMORY_ALLOCATOR_ID;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_PING, &ping) != 0) {
        ping_device_type_rc = -errno;
        goto out;
    }
    ping_device_type_rc = 0;

    memset(&request, 0, sizeof(request));
    request.size = requested_size;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_ALLOCATE_BLOCK, &request) != 0) {
        allocate_block_rc = -errno;
        goto out;
    }
    if (request.size == 0 || request.phys_addr == 0) {
        allocate_block_rc = -ERANGE;
        goto out;
    }
    allocate_block_rc = 0;

    memset(&ping, 0, sizeof(ping));
    ping.offset = request.offset;
    ping.size = request.size;
    ping.metadata = HOST_MEMORY_ALLOCATOR_COMMAND_ALLOCATE_ID;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_PING, &ping) != 0) {
        ping_allocate_rc = -errno;
        goto out;
    }
    ping_allocate_rc = (long)ping.metadata;
    if (ping_allocate_rc != 0) {
        goto out;
    }

    mapping = mmap(NULL, request.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)request.offset);
    if (mapping == MAP_FAILED) {
        mmap_rc = -errno;
        goto out;
    }
    mmap_rc = 0;
    ((volatile unsigned char *)mapping)[0] = ((volatile unsigned char *)mapping)[0];

    {
        const uint64_t invalid_offset = request.offset + request.size + (uint64_t)page_size;
        void *invalid_map = mmap(NULL, request.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)invalid_offset);
        if (invalid_map == MAP_FAILED) {
            invalid_mmap_rc = -errno;
        } else {
            invalid_mmap_rc = 0;
            munmap(invalid_map, request.size);
        }
    }

    memset(&claim, 0, sizeof(claim));
    claim.offset = request.offset;
    claim.size = request.size;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_CLAIM_SHARED, &claim) != 0) {
        claim_shared_rc = -errno;
        goto out;
    }
    claim_shared_rc = 0;

    unclaim_offset = claim.offset;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_UNCLAIM_SHARED, &unclaim_offset) != 0) {
        unclaim_shared_rc = -errno;
        goto out;
    }
    unclaim_shared_rc = 0;

    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_UNCLAIM_SHARED, &unclaim_offset) != 0) {
        second_unclaim_shared_rc = -errno;
    } else {
        second_unclaim_shared_rc = 0;
    }

    memset(&ping, 0, sizeof(ping));
    ping.offset = request.offset;
    ping.metadata = HOST_MEMORY_ALLOCATOR_COMMAND_UNALLOCATE_ID;
    if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_PING, &ping) != 0) {
        ping_unallocate_rc = -errno;
        goto out;
    }
    ping_unallocate_rc = 0;

out:
    if (mapping != MAP_FAILED) {
        munmap(mapping, request.size);
        mapping = MAP_FAILED;
    }

    if (allocate_block_rc == 0) {
        deallocate_offset = request.offset;
        if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_DEALLOCATE_BLOCK, &deallocate_offset) != 0) {
            deallocate_block_rc = -errno;
        } else {
            deallocate_block_rc = 0;
            if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_DEALLOCATE_BLOCK, &deallocate_offset) != 0) {
                deallocate_missing_rc = -errno;
            } else {
                deallocate_missing_rc = 0;
            }
        }
    }

    if (fd >= 0) {
        if (ioctl(fd, GOLDFISH_ADDRESS_SPACE_IOCTL_UNKNOWN, 0) != 0) {
            unknown_ioctl_rc = -errno;
        } else {
            unknown_ioctl_rc = 0;
        }
    }

    if (fd >= 0) {
        close_rc = close(fd) == 0 ? 0 : -errno;
        fd = -1;
    } else {
        close_rc = 0;
    }

    second_fd = open(DEVICE_NODE, O_RDWR | O_CLOEXEC);
    reopen_rc = second_fd >= 0 ? 0 : -errno;
    if (second_fd >= 0) {
        reclose_rc = close(second_fd) == 0 ? 0 : -errno;
        second_fd = -1;
    } else {
        reclose_rc = 0;
    }

    log_result("open_rc", open_rc);
    log_result("ping_device_type_rc", ping_device_type_rc);
    log_result("allocate_block_rc", allocate_block_rc);
    log_result("ping_allocate_rc", ping_allocate_rc);
    log_result("mmap_rc", mmap_rc);
    log_result("invalid_mmap_rc", invalid_mmap_rc);
    log_result("claim_shared_rc", claim_shared_rc);
    log_result("unclaim_shared_rc", unclaim_shared_rc);
    log_result("second_unclaim_shared_rc", second_unclaim_shared_rc);
    log_result("ping_unallocate_rc", ping_unallocate_rc);
    log_result("deallocate_block_rc", deallocate_block_rc);
    log_result("deallocate_missing_rc", deallocate_missing_rc);
    log_result("unknown_ioctl_rc", unknown_ioctl_rc);
    log_result("close_rc", close_rc);
    log_result("reopen_rc", reopen_rc);
    log_result("reclose_rc", reclose_rc);

    if (open_rc == 0 &&
        ping_device_type_rc == 0 &&
        allocate_block_rc == 0 &&
        ping_allocate_rc == 0 &&
        mmap_rc == 0 &&
        invalid_mmap_rc < 0 &&
        claim_shared_rc == 0 &&
        unclaim_shared_rc == 0 &&
        second_unclaim_shared_rc < 0 &&
        ping_unallocate_rc == 0 &&
        deallocate_block_rc == 0 &&
        deallocate_missing_rc < 0 &&
        unknown_ioctl_rc < 0 &&
        close_rc == 0 &&
        reopen_rc == 0 &&
        reclose_rc == 0) {
        result = 0;
    }

    printf("%s RESULT=%s\n", LOG_PREFIX, result == 0 ? "PASS" : "FAIL");
    return (int)result;
}
