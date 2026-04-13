// SPDX-License-Identifier: GPL-2.0-only

struct phy_device {
	int dummy;
};

struct phy_driver {
	int (*read_status)(struct phy_device *dev);
	int (*suspend)(struct phy_device *dev);
	int (*resume)(struct phy_device *dev);
	int (*soft_reset)(struct phy_device *dev);
	void (*link_change_notify)(struct phy_device *dev);
};

static int asix_ax88772a_read_status(struct phy_device *dev)
{
	(void)dev;
	return 0;
}

static int genphy_suspend(struct phy_device *dev)
{
	(void)dev;
	return 0;
}

static int genphy_resume(struct phy_device *dev)
{
	(void)dev;
	return 0;
}

static int asix_soft_reset(struct phy_device *dev)
{
	(void)dev;
	return 0;
}

static void asix_ax88772a_link_change_notify(struct phy_device *dev)
{
	(void)dev;
}

static struct phy_driver ax88796b_phy_driver = {
	.read_status = asix_ax88772a_read_status,
	.suspend = genphy_suspend,
	.resume = genphy_resume,
	.soft_reset = asix_soft_reset,
	.link_change_notify = asix_ax88772a_link_change_notify,
};
