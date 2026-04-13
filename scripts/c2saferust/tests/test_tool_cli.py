#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = REPO_ROOT / "scripts" / "c2saferust"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import intake  # noqa: E402
import oracle_runners  # noqa: E402
import smoke  # noqa: E402
import candidate_targets  # noqa: E402
import semantics  # noqa: E402
import tool_cli  # noqa: E402


class C2SafeRustToolTests(unittest.TestCase):
    SCRIPT_PATH = TOOL_DIR / "tool_cli.py"

    def _build_sample_root(self, root: Path, *, include_ax88796b_reference: bool = True) -> None:
        (root / "drivers" / "net").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "net" / "nlmon.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/ethtool.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/netdevice.h>
#include <linux/netlink.h>
#include <net/net_namespace.h>
#include <linux/if_arp.h>
#include <net/rtnetlink.h>

static netdev_tx_t nlmon_xmit(struct sk_buff *skb, struct net_device *dev)
{
\tdev_lstats_add(dev, skb->len);
\tdev_kfree_skb(skb);
\treturn NETDEV_TX_OK;
}

struct nlmon {
\tstruct netlink_tap nt;
};

static int nlmon_open(struct net_device *dev)
{
\tstruct nlmon *priv = netdev_priv(dev);
\tpriv->nt.dev = dev;
\tpriv->nt.module = THIS_MODULE;
\treturn netlink_add_tap(&priv->nt);
}

static int nlmon_close(struct net_device *dev)
{
\tstruct nlmon *priv = netdev_priv(dev);
\treturn netlink_remove_tap(&priv->nt);
}

static void nlmon_get_stats64(struct net_device *dev, struct rtnl_link_stats64 *stats)
{
\tdev_lstats_read(dev, &stats->rx_packets, &stats->rx_bytes);
}

static u32 always_on(struct net_device *dev)
{
\treturn 1;
}

static const struct ethtool_ops nlmon_ethtool_ops = {
\t.get_link = always_on,
};

static const struct net_device_ops nlmon_ops = {
\t.ndo_open = nlmon_open,
\t.ndo_stop = nlmon_close,
\t.ndo_start_xmit = nlmon_xmit,
\t.ndo_get_stats64 = nlmon_get_stats64,
};

static void nlmon_setup(struct net_device *dev)
{
\tdev->type = ARPHRD_NETLINK;
\tdev->priv_flags |= IFF_NO_QUEUE;
\tdev->lltx = true;
\tdev->netdev_ops = &nlmon_ops;
\tdev->ethtool_ops = &nlmon_ethtool_ops;
\tdev->needs_free_netdev = true;
\tdev->pcpu_stat_type = NETDEV_PCPU_STAT_LSTATS;
\tdev->mtu = NLMSG_GOODSIZE;
\tdev->min_mtu = sizeof(struct nlmsghdr);
}

static int nlmon_validate(struct nlattr *tb[], struct nlattr *data[],
\t\t\t  struct netlink_ext_ack *extack)
{
\tif (tb[IFLA_ADDRESS])
\t\treturn -EINVAL;
\treturn 0;
}

static struct rtnl_link_ops nlmon_link_ops = {
\t.kind = "nlmon",
\t.priv_size = sizeof(struct nlmon),
\t.setup = nlmon_setup,
\t.validate = nlmon_validate,
};

static int nlmon_register(void)
{
\treturn rtnl_link_register(&nlmon_link_ops);
}

static void nlmon_unregister(void)
{
\trtnl_link_unregister(&nlmon_link_ops);
}
"""
        )
        (root / "drivers" / "net" / "Makefile").write_text(
            "obj-$(CONFIG_NLMON) += nlmon.o\n"
            "obj-$(CONFIG_VSOCKMON) += vsockmon.o\n"
            "obj-$(CONFIG_DUMMY) += dummy.o\n"
        )
        (root / "drivers" / "net" / "Kconfig").write_text(
            'config NLMON\n'
            '\ttristate "Virtual netlink monitoring device"\n'
            '\thelp\n'
            '\t  Sample nlmon entry.\n'
            '\n'
            'config VSOCKMON\n'
            '\ttristate "Virtual vsock monitoring device"\n'
            '\thelp\n'
            '\t  Sample vsockmon entry.\n'
            '\n'
            'config DUMMY\n'
            '\ttristate "Dummy network driver"\n'
            '\thelp\n'
            '\t  Sample dummy entry.\n'
            '\n'
            'config NETKIT\n'
            '\tbool "Sample next symbol"\n'
        )
        (root / "drivers" / "net" / "vsockmon.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/ethtool.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/if_arp.h>
#include <linux/netdevice.h>
#include <net/rtnetlink.h>
#include <net/sock.h>
#include <net/af_vsock.h>
#include <uapi/linux/vsockmon.h>
#include <linux/virtio_vsock.h>

struct vsockmon {
\tstruct vsock_tap vt;
};

static int vsockmon_open(struct net_device *dev)
{
\tstruct vsockmon *priv = netdev_priv(dev);
\tpriv->vt.dev = dev;
\tpriv->vt.module = THIS_MODULE;
\treturn vsock_add_tap(&priv->vt);
}

static int vsockmon_close(struct net_device *dev)
{
\tstruct vsockmon *priv = netdev_priv(dev);
\treturn vsock_remove_tap(&priv->vt);
}

static netdev_tx_t vsockmon_xmit(struct sk_buff *skb, struct net_device *dev)
{
\tdev_lstats_add(dev, skb->len);
\tdev_kfree_skb(skb);
\treturn NETDEV_TX_OK;
}

static void vsockmon_get_stats64(struct net_device *dev, struct rtnl_link_stats64 *stats)
{
\tdev_lstats_read(dev, &stats->rx_packets, &stats->rx_bytes);
}

static int vsockmon_change_mtu(struct net_device *dev, int new_mtu)
{
\tWRITE_ONCE(dev->mtu, new_mtu);
\treturn 0;
}

static u32 always_on(struct net_device *dev)
{
\treturn 1;
}

static const struct ethtool_ops vsockmon_ethtool_ops = {
\t.get_link = always_on,
};

static const struct net_device_ops vsockmon_ops = {
\t.ndo_open = vsockmon_open,
\t.ndo_stop = vsockmon_close,
\t.ndo_start_xmit = vsockmon_xmit,
\t.ndo_get_stats64 = vsockmon_get_stats64,
\t.ndo_change_mtu = vsockmon_change_mtu,
};

static void vsockmon_setup(struct net_device *dev)
{
\tdev->type = ARPHRD_VSOCKMON;
\tdev->priv_flags |= IFF_NO_QUEUE;
\tdev->lltx = true;
\tdev->netdev_ops = &vsockmon_ops;
\tdev->ethtool_ops = &vsockmon_ethtool_ops;
\tdev->needs_free_netdev = true;
\tdev->features = NETIF_F_SG | NETIF_F_FRAGLIST | NETIF_F_HIGHDMA;
\tdev->flags = IFF_NOARP;
\tdev->mtu = VIRTIO_VSOCK_MAX_PKT_BUF_SIZE + sizeof(struct af_vsockmon_hdr);
\tdev->pcpu_stat_type = NETDEV_PCPU_STAT_LSTATS;
}

static struct rtnl_link_ops vsockmon_link_ops = {
\t.kind = "vsockmon",
\t.priv_size = sizeof(struct vsockmon),
\t.setup = vsockmon_setup,
};

static int vsockmon_register(void)
{
\treturn rtnl_link_register(&vsockmon_link_ops);
}

static void vsockmon_unregister(void)
{
\trtnl_link_unregister(&vsockmon_link_ops);
}
"""
        )
        (root / "drivers" / "net" / "dummy.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/ethtool.h>
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/netdevice.h>
#include <net/rtnetlink.h>

#define DRV_NAME "dummy"

static netdev_tx_t dummy_xmit(struct sk_buff *skb, struct net_device *dev)
{
\tdev_lstats_add(dev, skb->len);
\tskb_tx_timestamp(skb);
\tdev_kfree_skb(skb);
\treturn NETDEV_TX_OK;
}

static int dummy_dev_init(struct net_device *dev)
{
\tdev->pcpu_stat_type = NETDEV_PCPU_STAT_LSTATS;
\treturn 0;
}

static void set_multicast_list(struct net_device *dev)
{
\t(void)dev;
}

static int dummy_change_carrier(struct net_device *dev, bool new_carrier)
{
\tif (new_carrier)
\t\tnetif_carrier_on(dev);
\telse
\t\tnetif_carrier_off(dev);
\treturn 0;
}

static void dummy_get_stats64(struct net_device *dev, struct rtnl_link_stats64 *stats)
{
\tdev_lstats_read(dev, &stats->tx_packets, &stats->tx_bytes);
}

static int dummy_get_ts_info(struct net_device *dev, struct kernel_ethtool_ts_info *info)
{
\treturn ethtool_op_get_ts_info(dev, info);
}

static const struct ethtool_ops dummy_ethtool_ops = {
\t.get_ts_info = dummy_get_ts_info,
};

static const struct net_device_ops dummy_netdev_ops = {
\t.ndo_init = dummy_dev_init,
\t.ndo_start_xmit = dummy_xmit,
\t.ndo_validate_addr = eth_validate_addr,
\t.ndo_set_rx_mode = set_multicast_list,
\t.ndo_set_mac_address = eth_mac_addr,
\t.ndo_get_stats64 = dummy_get_stats64,
\t.ndo_change_carrier = dummy_change_carrier,
};

static void dummy_setup(struct net_device *dev)
{
\tether_setup(dev);
\tdev->netdev_ops = &dummy_netdev_ops;
\tdev->ethtool_ops = &dummy_ethtool_ops;
\tdev->needs_free_netdev = true;
\tdev->request_ops_lock = true;
\tdev->flags |= IFF_NOARP;
\tdev->flags &= ~IFF_MULTICAST;
\tdev->priv_flags |= IFF_LIVE_ADDR_CHANGE | IFF_NO_QUEUE;
\tdev->lltx = true;
\tdev->features |= NETIF_F_SG | NETIF_F_FRAGLIST;
\tdev->features |= NETIF_F_GSO_SOFTWARE;
\tdev->features |= NETIF_F_HW_CSUM | NETIF_F_HIGHDMA;
\tdev->features |= NETIF_F_GSO_ENCAP_ALL;
\tdev->hw_features |= dev->features;
\tdev->hw_enc_features |= dev->features;
\teth_hw_addr_random(dev);
\tdev->min_mtu = 0;
\tdev->max_mtu = 0;
}

static int dummy_validate(struct nlattr *tb[], struct nlattr *data[],
\t\t\t  struct netlink_ext_ack *extack)
{
\tif (tb[IFLA_ADDRESS]) {
\t\tif (nla_len(tb[IFLA_ADDRESS]) != ETH_ALEN)
\t\t\treturn -EINVAL;
\t\tif (!is_valid_ether_addr(nla_data(tb[IFLA_ADDRESS])))
\t\t\treturn -EADDRNOTAVAIL;
\t}
\treturn 0;
}

static struct rtnl_link_ops dummy_link_ops __read_mostly = {
\t.kind = DRV_NAME,
\t.setup = dummy_setup,
\t.validate = dummy_validate,
};

module_param(numdummies, int, 0);

static int __init dummy_init_one(void)
{
\tstruct net_device *dev_dummy;

\tdev_dummy = alloc_netdev(0, "dummy%d", NET_NAME_ENUM, dummy_setup);
\tif (!dev_dummy)
\t\treturn -ENOMEM;
\tdev_dummy->rtnl_link_ops = &dummy_link_ops;
\treturn register_netdevice(dev_dummy);
}

static int __init dummy_init_module(void)
{
\treturn rtnl_link_register(&dummy_link_ops);
}

static void __exit dummy_cleanup_module(void)
{
\trtnl_link_unregister(&dummy_link_ops);
}

module_init(dummy_init_module);
module_exit(dummy_cleanup_module);
"""
        )
        (root / "drivers" / "net" / "phy").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "net" / "phy" / "Makefile").write_text(
            "ifdef CONFIG_AX88796B_RUST_PHY\n"
            "  obj-$(CONFIG_AX88796B_PHY)\t+= ax88796b_rust.o\n"
            "else\n"
            "  obj-$(CONFIG_AX88796B_PHY)\t+= ax88796b.o\n"
            "endif\n"
        )
        (root / "drivers" / "net" / "phy" / "Kconfig").write_text(
            "config AX88796B_PHY\n"
            '\ttristate "Drivers for Asix PHYs"\n'
            '\thelp\n'
            '\t  Support for the Asix Electronics PHYs.\n'
            "\n"
            "config AX88796B_RUST_PHY\n"
            '\tbool "Rust reference driver for Asix PHYs"\n'
            "\tdepends on RUST_PHYLIB_ABSTRACTIONS && AX88796B_PHY\n"
            "\thelp\n"
            "\t  Uses the Rust reference driver for Asix PHYs (ax88796b_rust.ko).\n"
            "\n"
            "config RUST_PHYLIB_ABSTRACTIONS\n\tbool\n"
        )
        (root / "drivers" / "net" / "phy" / "ax88796b.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0+
#include <linux/kernel.h>
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/mii.h>
#include <linux/phy.h>

static int asix_soft_reset(struct phy_device *phydev)
{
\treturn genphy_soft_reset(phydev);
}

static int asix_ax88772a_read_status(struct phy_device *phydev)
{
\tint ret;

\tret = genphy_update_link(phydev);
\tif (ret)
\t\treturn ret;

\tif (!phydev->link)
\t\treturn 0;

\tret = genphy_read_lpa(phydev);
\tif (ret < 0)
\t\treturn ret;

\treturn 0;
}

static void asix_ax88772a_link_change_notify(struct phy_device *phydev)
{
\tif (phydev->state == PHY_NOLINK) {
\t\tphy_init_hw(phydev);
\t\t_phy_start_aneg(phydev);
\t}
}

static struct phy_driver asix_driver[] = {
{
\t.name\t\t= "Asix Electronics AX88772A",
\t.read_status\t= asix_ax88772a_read_status,
\t.suspend\t= genphy_suspend,
\t.resume\t\t= genphy_resume,
\t.soft_reset\t= asix_soft_reset,
\t.link_change_notify\t= asix_ax88772a_link_change_notify,
}, {
\t.name\t\t= "Asix Electronics AX88772C",
\t.suspend\t= genphy_suspend,
\t.resume\t\t= genphy_resume,
\t.soft_reset\t= asix_soft_reset,
}, {
\t.name\t\t= "Asix Electronics AX88796B",
\t.soft_reset\t= asix_soft_reset,
} };

module_phy_driver(asix_driver);
"""
        )
        if include_ax88796b_reference:
            (root / "drivers" / "net" / "phy" / "ax88796b_rust.rs").write_text("// reference\n")
        (root / "rust" / "bindings").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "bindings" / "bindings_helper.h").write_text(
            "#include <linux/ethtool.h>\n"
            "#include <linux/miscdevice.h>\n"
            "#include <linux/of_device.h>\n"
            "#include <linux/xarray.h>\n"
            "#include <trace/events/rust_sample.h>\n"
        )
        (root / "rust" / "helpers").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "helpers" / "helpers.c").write_text(
            "// SPDX-License-Identifier: GPL-2.0\n"
            "#define __rust_helper\n"
            "#include \"mutex.c\"\n"
            "#include \"of.c\"\n"
        )
        (root / "rust" / "helpers" / "mutex.c").write_text("// mutex\n")
        (root / "rust" / "helpers" / "of.c").write_text("// of\n")
        (root / "rust" / "kernel").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "net.rs").write_text(
            "#[cfg(CONFIG_RUST_PHYLIB_ABSTRACTIONS)]\npub mod phy;\n"
        )
        (root / "rust" / "kernel" / "net").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "net" / "phy.rs").write_text("// phy abstraction\n")
        (root / "rust" / "kernel" / "net" / "phy").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "net" / "phy" / "reg.rs").write_text("// reg\n")
        (root / "drivers" / "platform" / "goldfish").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "platform" / "goldfish" / "Kconfig").write_text(
            "# SPDX-License-Identifier: GPL-2.0-only\n"
            "menuconfig GOLDFISH\n"
            "\tbool \"Platform support for Goldfish virtual devices\"\n"
            "\n"
            "if GOLDFISH\n"
            "\n"
            "config GOLDFISH_PIPE\n"
            "\ttristate \"Goldfish virtual device for QEMU pipes\"\n"
            "\n"
            "endif # GOLDFISH\n"
        )
        (root / "drivers" / "platform" / "goldfish" / "Makefile").write_text(
            "# SPDX-License-Identifier: GPL-2.0-only\n"
            "obj-$(CONFIG_GOLDFISH_PIPE)\t+= goldfish_pipe.o\n"
        )
        (root / "rust" / "kernel" / "pci.rs").write_text(
            "pub trait Driver: Send {\n"
            "    type IdInfo: 'static;\n"
            "    fn probe(dev: &Device<device::Core>, id_info: &Self::IdInfo) -> impl PinInit<Self, Error>;\n"
            "    fn unbind(dev: &Device<device::Core>, this: Pin<&Self>) { let _ = (dev, this); }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "pci").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "pci" / "io.rs").write_text(
            "pub struct SharedMemoryBar;\n"
            "impl SharedMemoryBar {\n"
            "    pub fn phys_start(&self) -> bindings::resource_size_t { 0 }\n"
            "    pub fn map_into_vma(&self, vma: &VmaNew) -> Result { let _ = vma; let _ = (bindings::memremap, bindings::memunmap); Ok(()) }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "miscdevice.rs").write_text(
            "pub struct MiscDeviceRegistration<T>;\n"
            "pub trait MiscDevice {\n"
            "    type RegistrationData: Send + Sync + 'static;\n"
            "}\n"
            "impl<T> MiscDeviceRegistration<T> {\n"
            "    pub fn register_with_data() { let _ = (bindings::misc_register, bindings::misc_deregister); }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "uaccess.rs").write_text(
            "pub struct UserPtr;\n"
            "pub struct UserSlice;\n"
            "impl UserSlice { pub fn new(ptr: UserPtr, length: usize) -> Self { let _ = (ptr, length); Self } }\n"
        )
        (root / "rust" / "kernel" / "mm").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "mm" / "virt.rs").write_text(
            "pub struct PhysAddr;\n"
            "pub type Result = core::result::Result<(), ()>;\n"
            "pub struct VmaNew;\n"
            "impl VmaNew {\n"
            "    pub fn iomap_memory(&self, start: PhysAddr, len: usize) -> Result { let _ = (start, len, bindings::vm_iomap_memory); Ok(()) }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "page.rs").write_text(
            "pub struct PhysAddr;\n"
            "pub type Result = core::result::Result<(), ()>;\n"
            "pub struct Page;\n"
            "pub const PAGE_SIZE: usize = 4096;\n"
            "pub fn page_align(value: usize) -> usize { value }\n"
            "impl Page {\n"
            "    pub fn phys_addr(&self) -> PhysAddr { PhysAddr }\n"
            "    pub fn read_slice(&self, dst: &mut [u8], offset: usize) -> Result { let _ = (dst, offset); Ok(()) }\n"
            "    pub fn write_slice(&mut self, src: &[u8], offset: usize) -> Result { let _ = (src, offset); Ok(()) }\n"
            "    pub fn copy_from_user_slice(&mut self) -> Result { Ok(()) }\n"
            "    pub fn fill_zero(&mut self, offset: usize, len: usize) -> Result { let _ = (offset, len); Ok(()) }\n"
            "}\n"
        )

    def _build_watchdog_sample_root(self, root: Path) -> None:
        (root / "drivers" / "watchdog").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "watchdog" / "Makefile").write_text(
            "obj-$(CONFIG_SOFT_WATCHDOG) += softdog.o\n"
        )
        (root / "drivers" / "watchdog" / "Kconfig").write_text(
            "config SOFT_WATCHDOG\n"
            '\ttristate "Software watchdog"\n'
            "\thelp\n"
            "\t  Sample softdog entry.\n"
        )
        (root / "drivers" / "watchdog" / "softdog.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/watchdog.h>

static int softdog_start(struct watchdog_device *wdd)
{
\t(void)wdd;
\treturn 0;
}

static int softdog_stop(struct watchdog_device *wdd)
{
\t(void)wdd;
\treturn 0;
}

static const struct watchdog_ops softdog_ops = {
\t.start = softdog_start,
\t.stop = softdog_stop,
};

static struct watchdog_device softdog_dev = {
\t.ops = &softdog_ops,
};

static int __init softdog_init(void)
{
\treturn watchdog_register_device(&softdog_dev);
}
module_init(softdog_init);
"""
        )

    def _build_external_goldfish_source(self, root: Path) -> Path:
        source_root = root / "external-goldfish"
        (source_root / "goldfish_drivers").mkdir(parents=True, exist_ok=True)
        (source_root / "goldfish_drivers" / "goldfish_address_space.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include "defconfig_test.h"

#include <linux/fs.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/pci_ids.h>
#include <linux/pci_regs.h>
#include <linux/uaccess.h>
#include <goldfish/goldfish_address_space.h>

static int goldfish_address_space_open(struct inode *inode, struct file *file)
{
\t__get_free_page(GFP_KERNEL);
\tvirt_to_phys((void *)0);
\treturn 0;
}

static int goldfish_address_space_release(struct inode *inode, struct file *file)
{
\tfree_page(0);
\treturn 0;
}

static long goldfish_address_space_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
\tcopy_from_user((void *)0, (void __user *)arg, 0);
\tcopy_to_user((void __user *)arg, (void *)0, 0);
\treturn 0;
}

static long goldfish_address_space_compat_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
\treturn goldfish_address_space_ioctl(file, cmd, arg);
}

static int goldfish_address_space_mmap(struct file *file, struct vm_area_struct *vma)
{
\tremap_pfn_range(vma, vma->vm_start, 0, 0, vma->vm_page_prot);
\treturn 0;
}

static const struct file_operations goldfish_address_space_fops = {
\t.open = goldfish_address_space_open,
\t.release = goldfish_address_space_release,
\t.unlocked_ioctl = goldfish_address_space_ioctl,
\t.compat_ioctl = goldfish_address_space_compat_ioctl,
\t.mmap = goldfish_address_space_mmap,
};

static int goldfish_address_space_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
\tpci_enable_device(pdev);
\tpci_request_region(pdev, 0, "goldfish_address_space");
\tmemremap(0, 0, MEMREMAP_WB);
\tioremap(0, 0);
\tmisc_register((struct miscdevice *)0);
\treturn 0;
}

static void goldfish_address_space_remove(struct pci_dev *pdev)
{
\tmisc_deregister((struct miscdevice *)0);
\tpci_release_region(pdev, 0);
}

static struct pci_driver goldfish_address_space_driver = {
\t.name = "goldfish_address_space",
\t.probe = goldfish_address_space_probe,
\t.remove = goldfish_address_space_remove,
};
"""
        )
        return source_root

    def _build_rnull_sample_root(self, root: Path) -> None:
        self._build_sample_root(root)

        (root / "drivers" / "block" / "null_blk").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "block" / "rnull").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "block" / "Kconfig").write_text(
            "# SPDX-License-Identifier: GPL-2.0\n"
            'source "drivers/block/null_blk/Kconfig"\n'
            'source "drivers/block/rnull/Kconfig"\n'
        )
        (root / "drivers" / "block" / "Makefile").write_text(
            "# SPDX-License-Identifier: GPL-2.0\n"
            "obj-$(CONFIG_BLK_DEV_NULL_BLK)\t+= null_blk/\n"
            "obj-$(CONFIG_BLK_DEV_RUST_NULL) += rnull/\n"
        )
        (root / "drivers" / "block" / "null_blk" / "Kconfig").write_text(
            "config BLK_DEV_NULL_BLK\n"
            '\ttristate "Null block driver"\n'
            "\n"
        )
        (root / "drivers" / "block" / "null_blk" / "Makefile").write_text(
            "obj-$(CONFIG_BLK_DEV_NULL_BLK) += main.o\n"
        )
        (root / "drivers" / "block" / "null_blk" / "null_blk.h").write_text("// private header\n")
        (root / "drivers" / "block" / "null_blk" / "main.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/sched.h>
#include <linux/fs.h>
#include <linux/init.h>
#include "null_blk.h"

struct nullb_device {
\tbool power;
\tunsigned int blocksize;
\tbool rotational;
\tunsigned long size;
\tunsigned int irqmode;
};

struct nullb {
\tstruct nullb_device *dev;
\tchar disk_name[32];
};

struct nullb_queue {
\tstruct nullb_device *dev;
};

struct nullb_cmd {
\tint error;
};

static struct blk_mq_tag_set tag_set;

static int null_add_dev(struct nullb *nullb)
{
\t/* logical_block_size physical_block_size disk_name SECTOR_SHIFT */
\treturn 0;
}

static void null_del_dev(struct nullb *nullb)
{
\t(void)nullb;
}

static ssize_t nullb_device_power_show(struct config_item *item, char *page)
{
\treturn 0;
}

static ssize_t nullb_device_power_store(struct config_item *item, const char *page, size_t count)
{
\tnull_add_dev((struct nullb *)0);
\tnull_del_dev((struct nullb *)0);
\treturn count;
}

static ssize_t memb_group_features_show(struct config_item *item, char *page)
{
\treturn 0;
}

#define NULLB_DEVICE_ATTR(_name, _type, _validate)
NULLB_DEVICE_ATTR(size, ulong, NULL);
NULLB_DEVICE_ATTR(blocksize, uint, NULL);
NULLB_DEVICE_ATTR(rotational, bool, NULL);
NULLB_DEVICE_ATTR(irqmode, uint, NULL);

static struct configfs_attribute *nullb_device_attrs[] = {
\t&nullb_device_attr_power,
\t&nullb_device_attr_blocksize,
\t&nullb_device_attr_rotational,
\t&nullb_device_attr_size,
\t&nullb_device_attr_irqmode,
\tNULL,
};

static struct configfs_attribute *nullb_group_attrs[] = {
\t&memb_group_attr_features,
\tNULL,
};

static void nullb_device_release(struct config_item *item)
{
\t(void)item;
}

static const struct configfs_item_operations nullb_device_ops = {
\t.release = nullb_device_release,
};

static struct config_group *nullb_group_make_group(struct config_group *group, const char *name)
{
\tconfig_group_init_type_name((struct config_group *)0, name, (struct config_item_type *)0);
\treturn group;
}

static void nullb_group_drop_item(struct config_group *group, struct config_item *item)
{
\tconfig_item_put(item);
}

static const struct configfs_group_operations nullb_group_ops = {
\t.make_group = nullb_group_make_group,
\t.drop_item = nullb_group_drop_item,
};

static blk_status_t null_queue_rq(struct blk_mq_hw_ctx *hctx, const struct blk_mq_queue_data *bd)
{
\tstruct request *rq = bd->rq;
\tblk_rq_sectors(rq);
\tblk_rq_pos(rq);
\tblk_mq_start_request(rq);
\tblk_mq_complete_request(rq);
\treturn BLK_STS_OK;
}

static void null_complete_rq(struct request *rq)
{
\tblk_mq_end_request(rq, BLK_STS_OK);
}

static const struct blk_mq_ops null_mq_ops = {
\t.queue_rq = null_queue_rq,
\t.complete = null_complete_rq,
};
"""
        )
        (root / "drivers" / "block" / "rnull" / "Kconfig").write_text(
            "# SPDX-License-Identifier: GPL-2.0\n"
            "config BLK_DEV_RUST_NULL\n"
            '\ttristate "Rust null block driver (Experimental)"\n'
            "\tdepends on RUST && CONFIGFS_FS\n"
        )
        (root / "drivers" / "block" / "rnull" / "Makefile").write_text(
            "obj-$(CONFIG_BLK_DEV_RUST_NULL) += rnull_mod.o\n"
            "rnull_mod-y := rnull.o\n"
        )
        (root / "drivers" / "block" / "rnull" / "rnull.rs").write_text(
            """#![forbid(unsafe_code)]

mod configfs;

use configfs::IRQMode;
use kernel::{
    block::{
        self,
        mq::{
            self,
            gen_disk::{self, GenDisk},
            Operations, TagSet,
        },
    },
    prelude::*,
    sync::{aref::ARef, Arc},
};

module! {
    type: NullBlkModule,
    name: "rnull_mod",
    authors: ["Example"],
    description: "Rust null block driver",
    license: "GPL v2",
}

#[pin_data]
struct NullBlkModule {
    #[pin]
    configfs_subsystem: kernel::configfs::Subsystem<configfs::Config>,
}

impl kernel::InPlaceModule for NullBlkModule {
    fn init(_module: &'static ThisModule) -> impl PinInit<Self, Error> {
        try_pin_init!(Self {
            configfs_subsystem <- configfs::subsystem(),
        })
    }
}

struct NullBlkDevice;

struct QueueData {
    irq_mode: IRQMode,
}

impl NullBlkDevice {
    fn new(
        name: &CStr,
        block_size: u32,
        rotational: bool,
        capacity_mib: u64,
        irq_mode: IRQMode,
    ) -> Result<GenDisk<Self>> {
        let tagset = Arc::pin_init(TagSet::new(1, 256, 1), GFP_KERNEL)?;
        let queue_data = Box::new(QueueData { irq_mode }, GFP_KERNEL)?;
        gen_disk::GenDiskBuilder::new()
            .capacity_sectors(capacity_mib << (20 - block::SECTOR_SHIFT))
            .logical_block_size(block_size)?
            .physical_block_size(block_size)?
            .rotational(rotational)
            .build(fmt!("{}", name.to_str()?), tagset, queue_data)
    }
}

#[vtable]
impl Operations for NullBlkDevice {
    type QueueData = KBox<QueueData>;

    fn queue_rq(queue_data: &QueueData, rq: ARef<mq::Request<Self>>, _is_last: bool) -> Result {
        match queue_data.irq_mode {
            IRQMode::None => mq::Request::end_ok(rq)
                .map_err(|_e| kernel::error::code::EIO)
                .expect("request completion should succeed"),
            IRQMode::Soft => mq::Request::complete(rq),
        }
        Ok(())
    }

    fn commit_rqs(_queue_data: &QueueData) {}

    fn complete(rq: ARef<mq::Request<Self>>) {
        mq::Request::end_ok(rq)
            .map_err(|_e| kernel::error::code::EIO)
            .expect("request completion should succeed");
    }
}
"""
        )
        (root / "drivers" / "block" / "rnull" / "configfs.rs").write_text(
            """use super::{NullBlkDevice, THIS_MODULE};
use kernel::{
    block::mq::gen_disk::{GenDisk, GenDiskBuilder},
    configfs::{self, AttributeOperations},
    configfs_attrs,
    fmt::{self, Write as _},
    new_mutex,
    page::PAGE_SIZE,
    prelude::*,
    str::{kstrtobool_bytes, CString},
    sync::Mutex,
};

pub(crate) fn subsystem() -> impl PinInit<kernel::configfs::Subsystem<Config>, Error> {
    let item_type = configfs_attrs! {
        container: configfs::Subsystem<Config>,
        data: Config,
        child: DeviceConfig,
        attributes: [
            features: 0,
        ],
    };
    kernel::configfs::Subsystem::new(c"rnull", item_type, try_pin_init!(Config {}))
}

#[pin_data]
pub(crate) struct Config {}

#[vtable]
impl AttributeOperations<0> for Config {
    type Data = Config;

    fn show(_this: &Config, page: &mut [u8; PAGE_SIZE]) -> Result<usize> {
        let mut writer = kernel::str::Formatter::new(page);
        writer.write_str("blocksize,size,rotational,irqmode\\n")?;
        Ok(writer.bytes_written())
    }
}

#[vtable]
impl configfs::GroupOperations for Config {
    type Child = DeviceConfig;

    fn make_group(&self, name: &CStr) -> Result<impl PinInit<configfs::Group<DeviceConfig>, Error>> {
        let item_type = configfs_attrs! {
            container: configfs::Group<DeviceConfig>,
            data: DeviceConfig,
            attributes: [
                power: 0,
                blocksize: 1,
                rotational: 2,
                size: 3,
                irqmode: 4,
            ],
        };

        Ok(configfs::Group::new(
            name.try_into()?,
            item_type,
            try_pin_init!(DeviceConfig {
                data <- new_mutex!(DeviceConfigInner {
                    powered: false,
                    block_size: 4096,
                    rotational: false,
                    disk: None,
                    capacity_mib: 4096,
                    irq_mode: IRQMode::None,
                    name: name.try_into()?,
                }),
            }),
        ))
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum IRQMode {
    None,
    Soft,
}

impl TryFrom<u8> for IRQMode {
    type Error = kernel::error::Error;
    fn try_from(value: u8) -> Result<Self> {
        match value {
            0 => Ok(Self::None),
            1 => Ok(Self::Soft),
            _ => Err(EINVAL),
        }
    }
}

#[pin_data]
pub(crate) struct DeviceConfig {
    #[pin]
    data: Mutex<DeviceConfigInner>,
}

#[pin_data]
struct DeviceConfigInner {
    powered: bool,
    name: CString,
    block_size: u32,
    rotational: bool,
    capacity_mib: u64,
    irq_mode: IRQMode,
    disk: Option<GenDisk<NullBlkDevice>>,
}
"""
        )
        (root / "rust" / "kernel" / "block").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "block" / "mq").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "block.rs").write_text(
            "pub mod mq;\n"
            "pub const SECTOR_SHIFT: u32 = bindings::SECTOR_SHIFT;\n"
        )
        (root / "rust" / "kernel" / "block" / "mq.rs").write_text(
            "pub mod gen_disk;\n"
            "mod operations;\n"
            "mod request;\n"
            "mod tag_set;\n"
            "pub use operations::Operations;\n"
            "pub use request::Request;\n"
            "pub use tag_set::TagSet;\n"
        )
        (root / "rust" / "kernel" / "block" / "mq" / "operations.rs").write_text(
            "pub trait Operations: Sized {\n"
            "    fn queue_rq(\n"
            "        queue_data: (),\n"
            "        rq: ARef<Request<Self>>,\n"
            "        is_last: bool,\n"
            "    ) -> Result;\n"
            "    fn complete(rq: ARef<Request<Self>>);\n"
            "}\n"
            "// unsafe extern \"C\" fn queue_rq_callback(\n"
            "// unsafe extern \"C\" fn complete_callback(\n"
            "// let ret = T::queue_rq(\n"
            "// T::complete(aref);\n"
        )
        (root / "rust" / "kernel" / "block" / "mq" / "gen_disk.rs").write_text(
            "pub struct GenDiskBuilder {\n"
            "    _private: (),\n"
            "}\n"
            "impl GenDiskBuilder {\n"
            "    pub fn build<T: Operations>(\n"
            "        self,\n"
            "        name: String,\n"
            "        tagset: Arc<TagSet<T>>,\n"
            "        queue_data: T::QueueData,\n"
            "    ) -> Result<GenDisk<T>> {\n"
            "        let _ = (name, tagset, queue_data);\n"
            "        Err(EINVAL)\n"
            "    }\n"
            "}\n"
            "pub struct GenDisk<T: Operations> {\n"
            "    _private: core::marker::PhantomData<T>,\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "block" / "mq" / "request.rs").write_text(
            "pub struct Request<T: Operations> {\n"
            "    _private: core::marker::PhantomData<T>,\n"
            "}\n"
            "impl<T: Operations> Request<T> {\n"
            "    pub fn end_ok(this: ARef<Self>) { let _ = this; }\n"
            "    pub fn complete(this: ARef<Self>) { let _ = this; }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "block" / "mq" / "tag_set.rs").write_text(
            "pub struct TagSet<T: Operations> {\n"
            "    _private: core::marker::PhantomData<T>,\n"
            "}\n"
            "impl<T: Operations> TagSet<T> {\n"
            "    pub fn new(_nr_hw_queues: u32, _queue_depth: u32, _numa_node: u32) -> Self {\n"
            "        Self { _private: core::marker::PhantomData }\n"
            "    }\n"
            "}\n"
            "impl<T: Operations> PinnedDrop for TagSet<T> {\n"
            "    fn drop(self: Pin<&mut Self>) {}\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "configfs.rs").write_text(
            "pub struct Subsystem<Data> {\n"
            "    _private: core::marker::PhantomData<Data>,\n"
            "}\n"
            "// bindings::configfs_register_subsystem\n"
            "// bindings::configfs_unregister_subsystem\n"
            "// unsafe extern \"C\" fn make_group(\n"
            "// unsafe extern \"C\" fn drop_item(\n"
            "pub trait GroupOperations {\n"
            "    type Child: 'static;\n"
            "}\n"
            "pub trait AttributeOperations<const ID: u64 = 0> {\n"
            "    fn show(&self) {}\n"
            "    fn store(&self) {}\n"
            "}\n"
        )

    def _apply_realized_mvp_state(self, root: Path) -> None:
        (root / "drivers" / "net" / "Kconfig").write_text(
            'config NLMON\n'
            '\ttristate "Virtual netlink monitoring device"\n'
            '\thelp\n'
            '\t  Sample nlmon entry.\n'
            '\n'
            'config NLMON_RUST\n'
            '\tbool "Rust implementation of nlmon"\n'
            '\tdepends on RUST && NLMON\n'
            '\thelp\n'
            '\t  Builds the Rust implementation of nlmon (nlmon_rust.ko)\n'
            '\t  instead of the original C implementation (nlmon.ko).\n'
            '\n'
            'config NETKIT\n'
            '\tbool "Sample next symbol"\n'
        )
        (root / "drivers" / "net" / "Makefile").write_text(
            "ifdef CONFIG_NLMON_RUST\n"
            "  obj-$(CONFIG_NLMON) += nlmon_rust.o\n"
            "else\n"
            "  obj-$(CONFIG_NLMON) += nlmon.o\n"
            "endif\n"
        )
        (root / "rust" / "bindings" / "bindings_helper.h").write_text(
            "#include <linux/ethtool.h>\n"
            "#include <linux/if_arp.h>\n"
            "#include <linux/miscdevice.h>\n"
            "#include <linux/netdevice.h>\n"
            "#include <linux/netlink.h>\n"
            "#include <linux/of_device.h>\n"
            "#include <linux/xarray.h>\n"
            "#include <net/rtnetlink.h>\n"
            "#include <trace/events/rust_sample.h>\n"
        )
        (root / "rust" / "helpers" / "helpers.c").write_text(
            "// SPDX-License-Identifier: GPL-2.0\n"
            "#define __rust_helper\n"
            "#include \"mutex.c\"\n"
            "#include \"net.c\"\n"
            "#include \"of.c\"\n"
        )
        (root / "rust" / "helpers" / "net.c").write_text(
            "// SPDX-License-Identifier: GPL-2.0\n\n"
            "#include <linux/netdevice.h>\n"
            "#include <linux/skbuff.h>\n\n"
            "__rust_helper void rust_helper_dev_kfree_skb(struct sk_buff *skb)\n"
            "{\n\tdev_kfree_skb(skb);\n}\n\n"
            "__rust_helper void rust_helper_dev_lstats_add(struct net_device *dev, unsigned int len)\n"
            "{\n\tdev_lstats_add(dev, len);\n}\n\n"
            "__rust_helper void *rust_helper_netdev_priv(const struct net_device *dev)\n"
            "{\n\treturn netdev_priv(dev);\n}\n"
        )
        (root / "rust" / "kernel" / "net.rs").write_text(
            "#[cfg(CONFIG_RUST_PHYLIB_ABSTRACTIONS)]\npub mod phy;\n"
            "pub mod netdevice;\n"
            "pub mod netlink_tap;\n"
            "pub mod rtnl;\n"
            "pub mod skbuff;\n"
            "pub mod stats;\n"
        )
        (root / "rust" / "kernel" / "net" / "netdevice.rs").write_text("// netdevice abstraction\n")
        (root / "rust" / "kernel" / "net" / "netlink_tap.rs").write_text("// tap abstraction\n")
        (root / "rust" / "kernel" / "net" / "rtnl.rs").write_text("// rtnl abstraction\n")
        (root / "rust" / "kernel" / "net" / "skbuff.rs").write_text("// skbuff abstraction\n")
        (root / "rust" / "kernel" / "net" / "stats.rs").write_text("// stats abstraction\n")

    def _apply_safety_hardened_state(self, root: Path, *, driver_unsafe: bool = False, driver_bindings: bool = False) -> None:
        self._apply_realized_mvp_state(root)
        (root / "drivers" / "net" / "nlmon_rust.rs").write_text(
            (
                "#![forbid(unsafe_code)]\n\n"
                "use kernel::{net::{netdevice, netlink_tap, rtnl, skbuff, stats}, prelude::*};\n\n"
                "#[pin_data]\n"
                + (
                    "unsafe impl Zeroable for NlmonPriv {}\n"
                    if driver_unsafe
                    else "#[derive(Zeroable)]\n"
                )
                + "#[repr(C)]\n"
                "struct NlmonPriv {\n"
                "\t#[pin]\n"
                "\ttap: netlink_tap::Tap,\n"
                "}\n\n"
                "struct NlmonDriver;\n\n"
                "impl netdevice::Operations for NlmonDriver {\n"
                "\ttype Private = NlmonPriv;\n\n"
                "\tfn open(dev: &mut netdevice::Device, private: Pin<&mut Self::Private>) -> Result {\n"
                "\t\tprivate.project().tap.add(dev, &THIS_MODULE)\n"
                "\t}\n\n"
                "\tfn stop(_dev: &mut netdevice::Device, private: Pin<&mut Self::Private>) -> Result {\n"
                "\t\tprivate.project().tap.remove()\n"
                "\t}\n"
                "\n"
                "\tfn start_xmit(skb: skbuff::SkBuff, dev: &netdevice::Device) -> netdevice::TxOutcome {\n"
                "\t\tstats::dev_lstats_add(dev, skb.len());\n"
                "\t\tnetdevice::TxOutcome::Ok\n"
                "\t}\n"
                "}\n\n"
                "impl rtnl::Driver for NlmonDriver {\n"
                "\tconst KIND: &'static CStr = c\"nlmon\";\n\n"
                "\tfn validate(ctx: &mut rtnl::ValidateContext<'_>) -> Result {\n"
                "\t\tif ctx.has_link_attr("
                + ("bindings::IFLA_ADDRESS as usize" if driver_bindings else "rtnl::LinkAttr::ADDRESS")
                + ") {\n"
                "\t\t\treturn Err(EINVAL);\n"
                "\t\t}\n"
                "\t\tOk(())\n"
                "\t}\n"
                "}\n"
            )
        )
        (root / "rust" / "kernel" / "net" / "netdevice.rs").write_text(
            "use crate::{bindings, net::skbuff, prelude::*};\n"
            "pub mod features { pub const SG: u64 = 1; pub const FRAGLIST: u64 = 2; pub const HIGHDMA: u64 = 4; }\n"
            "pub mod mtu { pub const fn nlmsg_goodsize() -> u32 { 4096 } pub const NLMSGHDR: u32 = 16; }\n"
            "pub mod device_type { pub const NETLINK: u16 = 0; }\n"
            "pub mod flags { pub const NO_ARP: u32 = 0; }\n"
            "pub mod priv_flags { pub const NO_QUEUE: u32 = 0; }\n"
            "pub mod pcpu_stat_type { pub const LSTATS: u32 = 0; }\n"
            "pub enum TxOutcome { Ok, Busy(skbuff::SkBuff) }\n"
            "pub struct Device;\n"
            "impl Device {\n"
            "\tpub(crate) fn as_ptr(&self) -> *mut bindings::net_device { core::ptr::null_mut() }\n"
            "\tpub(crate) unsafe fn from_raw_ref<'a>(ptr: *mut bindings::net_device) -> &'a Self { let _ = ptr; unimplemented!() }\n"
            "}\n"
            "pub trait Operations { type Private: Zeroable; fn open(_dev: &mut Device, _private: Pin<&mut Self::Private>) -> Result; fn stop(_dev: &mut Device, _private: Pin<&mut Self::Private>) -> Result; fn start_xmit(skb: skbuff::SkBuff, dev: &Device) -> TxOutcome; }\n"
            "struct OperationsVTable<T: Operations>(core::marker::PhantomData<T>);\n"
            "impl<T: Operations> OperationsVTable<T> {\n"
            "\textern \"C\" fn start_xmit_callback(skb: *mut bindings::sk_buff, dev: *mut bindings::net_device) -> bindings::netdev_tx {\n"
            "\t\tlet dev = unsafe { Device::from_raw_ref(dev) };\n"
            "\t\tlet skb = unsafe { skbuff::SkBuff::from_raw_owned(skb) };\n"
            "\t\tmatch T::start_xmit(skb, dev) { TxOutcome::Ok => bindings::netdev_tx_NETDEV_TX_OK, TxOutcome::Busy(skb) => { let _ = skb.into_raw(); bindings::netdev_tx_NETDEV_TX_BUSY } }\n"
            "\t}\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "net" / "netlink_tap.rs").write_text(
            "use crate::{prelude::*, ThisModule};\n"
            "use core::pin::Pin;\n"
            "#[derive(Zeroable)]\n"
            "pub struct Tap { registered: bool }\n"
            "impl Tap {\n"
            "\tpub fn add(\n"
            "\t\tself: Pin<&mut Self>,\n"
            "\t\t_dev: &crate::net::netdevice::Device,\n"
            "\t\t_module: &'static ThisModule,\n"
            "\t) -> Result { Ok(()) }\n"
            "\tpub fn remove(self: Pin<&mut Self>) -> Result { Ok(()) }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "net" / "rtnl.rs").write_text(
            "use crate::prelude::*;\n"
            "pub struct LinkAttr(usize);\n"
            "impl LinkAttr { pub const ADDRESS: Self = Self(1); }\n"
            "pub struct InfoAttr(usize);\n"
            "impl InfoAttr { pub const KIND: Self = Self(1); pub const DATA: Self = Self(2); }\n"
            "const LINK_ATTR_TABLE_LEN: usize = bindings::__IFLA_MAX as usize;\n"
            "const INFO_ATTR_TABLE_LEN: usize = bindings::__IFLA_INFO_MAX as usize;\n"
            "pub struct ValidateContext<'a> { _p: core::marker::PhantomData<&'a ()> }\n"
            "impl<'a> ValidateContext<'a> { pub fn has_link_attr(&self, attr: LinkAttr) -> bool { let _ = attr; false } pub fn has_info_attr(&self, attr: InfoAttr) -> bool { let _ = attr; false } }\n"
            "struct NlAttrTable;\n"
            "impl NlAttrTable { fn new(tb: *mut *mut (), len: usize) -> Self { let _ = (tb, len, LINK_ATTR_TABLE_LEN, INFO_ATTR_TABLE_LEN); Self } }\n"
            "pub trait Driver: crate::net::netdevice::Operations { const KIND: &'static CStr; }\n"
            "pub struct Registration<T: Driver> { _p: core::marker::PhantomData<T> }\n"
            "impl<T: Driver> Registration<T> { pub fn new() -> impl PinInit<Self, Error> { build_assert!(!core::mem::needs_drop::<T::Private>()); pin_init!(Self { _p: core::marker::PhantomData, }) } }\n"
        )
        (root / "rust" / "kernel" / "net" / "skbuff.rs").write_text(
            "use crate::bindings;\n"
            "pub struct SkBuff;\n"
            "impl SkBuff {\n"
            "\tpub(crate) unsafe fn from_raw_owned(_ptr: *mut bindings::sk_buff) -> Self { Self }\n"
            "\tpub fn len(&self) -> u32 { 0 }\n"
            "\tpub(crate) fn into_raw(mut self) -> *mut bindings::sk_buff { let _ = &mut self; core::ptr::null_mut() }\n"
            "}\n"
            "impl Drop for SkBuff { fn drop(&mut self) {} }\n"
        )
        (root / "rust" / "kernel" / "net" / "stats.rs").write_text(
            "use crate::{bindings, net::netdevice};\n"
            "pub fn dev_lstats_add(dev: &netdevice::Device, len: u32) {\n"
            "\tlet _ = (dev, len);\n"
            "\tunsafe { bindings::dev_lstats_add(dev.as_ptr(), len) };\n"
            "}\n"
        )

    def _apply_goldfish_command_semantics_hardened_state(self, root: Path) -> None:
        (root / "drivers" / "platform" / "goldfish" / "goldfish_address_space.rs").write_text(
            """#![forbid(unsafe_code)]

type Result<T = ()> = core::result::Result<T, i32>;
type PhysAddr = u64;

struct ControlBar;

impl ControlBar {
    fn write32(&self, _value: u32, _offset: usize) {}
    fn read32(&self, _offset: usize) -> u32 { 0 }
}

struct DeviceRuntime;

impl DeviceRuntime {
    fn control_bar(&self) -> Result<ControlBar> { Ok(ControlBar) }
    fn run_command_locked(_control: &ControlBar, _command: CommandId) -> Result { Ok(()) }
    fn issue_command_locked(_control: &ControlBar, _command: CommandId) {}
    fn write_u64(_control: &ControlBar, _low: usize, _high: usize, _value: u64) {}
    fn read_u64(_control: &ControlBar, _low: usize, _high: usize) -> u64 { 0 }

    fn generate_handle(&self) -> Result<u32> {
        let control = self.control_bar()?;
        Self::issue_command_locked(&control, CommandId::GenHandle);
        let handle = control.read32(Registers::HANDLE);
        if handle == GOLDFISH_AS_INVALID_HANDLE {
            return Err(-22);
        }
        Ok(handle)
    }

    fn tell_ping_info_addr(&self, handle: u32, ping_info_phys: PhysAddr) -> Result {
        let control = self.control_bar()?;
        control.write32(handle, Registers::HANDLE);
        Self::write_u64(
            &control,
            Registers::PING_INFO_ADDR_LOW,
            Registers::PING_INFO_ADDR_HIGH,
            ping_info_phys,
        );
        Self::issue_command_locked(&control, CommandId::TellPingInfoAddr);
        let returned = Self::read_u64(
            &control,
            Registers::PING_INFO_ADDR_LOW,
            Registers::PING_INFO_ADDR_HIGH,
        );
        if returned != ping_info_phys {
            return Err(-22);
        }
        Ok(())
    }

    fn destroy_handle(&self, handle: u32) -> Result {
        let control = self.control_bar()?;
        control.write32(handle, Registers::HANDLE);
        Self::issue_command_locked(&control, CommandId::DestroyHandle);
        Ok(())
    }

    fn allocate_block(&self, size: u64) -> Result<u64> {
        let control = self.control_bar()?;
        Self::write_u64(
            &control,
            Registers::BLOCK_SIZE_LOW,
            Registers::BLOCK_SIZE_HIGH,
            size,
        );
        Self::run_command_locked(&control, CommandId::AllocateBlock)?;
        Ok(Self::read_u64(
            &control,
            Registers::BLOCK_OFFSET_LOW,
            Registers::BLOCK_OFFSET_HIGH,
        ))
    }

    fn deallocate_block(&self, offset: u64) -> Result {
        let control = self.control_bar()?;
        Self::write_u64(
            &control,
            Registers::BLOCK_OFFSET_LOW,
            Registers::BLOCK_OFFSET_HIGH,
            offset,
        );
        Self::run_command_locked(&control, CommandId::DeallocateBlock)
    }
}

enum CommandId {
    AllocateBlock,
    DeallocateBlock,
    GenHandle,
    DestroyHandle,
    TellPingInfoAddr,
}

struct Registers;

impl Registers {
    const HANDLE: usize = 0;
    const PING_INFO_ADDR_LOW: usize = 1;
    const PING_INFO_ADDR_HIGH: usize = 2;
    const BLOCK_SIZE_LOW: usize = 3;
    const BLOCK_SIZE_HIGH: usize = 4;
    const BLOCK_OFFSET_LOW: usize = 5;
    const BLOCK_OFFSET_HIGH: usize = 6;
}

const GOLDFISH_AS_INVALID_HANDLE: u32 = u32::MAX;
"""
        )

    def test_build_kbuild_plan_detects_missing_rust_switch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            payload = intake.build_kbuild_plan("drivers/net/nlmon.c", repo_root=root)
            self.assertEqual(payload["module_id"], "nlmon")
            self.assertEqual(payload["source_config_symbol"], "NLMON")
            self.assertEqual(payload["suggested_rust_config_symbol"], "NLMON_RUST")
            self.assertFalse(payload["current_state"]["current_rust_switch_present"])
            self.assertIn("obj-$(CONFIG_NLMON) += nlmon_rust.o", "\n".join(payload["suggested_makefile_snippet"]))

    def test_binding_and_helper_audits_detect_expected_gaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            binding_payload = intake.build_binding_gap_audit("drivers/net/nlmon.c", repo_root=root)
            helper_payload = intake.build_helper_audit("drivers/net/nlmon.c", repo_root=root)

            self.assertIn("linux/netdevice.h", binding_payload["candidate_missing_binding_headers"])
            self.assertIn("linux/netlink.h", binding_payload["candidate_missing_binding_headers"])
            self.assertNotIn(
                "dev_kfree_skb",
                {entry["symbol"] for entry in binding_payload["direct_ffi_symbols"]},
            )
            helper_symbols = {entry["symbol"] for entry in helper_payload["required_helper_wrappers"]}
            self.assertEqual(helper_symbols, {"dev_kfree_skb", "dev_lstats_add", "netdev_priv"})

    def test_patch_plans_capture_binding_and_helper_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            kbuild_patch = intake.build_kbuild_patch_plan("drivers/net/nlmon.c", repo_root=root)
            bindings_patch = intake.build_bindings_patch_plan("drivers/net/nlmon.c", repo_root=root)
            helpers_patch = intake.build_helpers_patch_plan("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(kbuild_patch["artifact_type"], "kbuild-patch-plan")
            self.assertEqual(len(kbuild_patch["patch_units"]), 2)

            binding_additions = {entry["header"] for entry in bindings_patch["binding_additions"]}
            self.assertEqual(
                binding_additions,
                {"linux/if_arp.h", "linux/netdevice.h", "linux/netlink.h", "net/rtnetlink.h"},
            )
            self.assertEqual(
                {entry["header"] for entry in bindings_patch["rust_abstraction_headers"]},
                {"linux/kernel.h", "linux/module.h"},
            )
            self.assertEqual(
                {entry["header"] for entry in bindings_patch["deferred_headers"]},
                {"net/net_namespace.h"},
            )

            helper_specs = {entry["symbol"] for entry in helpers_patch["wrapper_specs"]}
            self.assertEqual(helper_specs, {"dev_kfree_skb", "dev_lstats_add", "netdev_priv"})
            self.assertEqual(helpers_patch["patch_units"][1]["insert_lines"], ['#include "net.c"'])

    def test_abstraction_plan_and_unsafe_obligations_capture_mvp_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            abstraction_payload = intake.build_abstraction_plan("drivers/net/nlmon.c", repo_root=root)
            unsafe_payload = intake.build_unsafe_obligations("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(
                abstraction_payload["mvp_scope"]["required_callbacks"],
                ["nlmon_setup", "nlmon_validate", "nlmon_open", "nlmon_close", "nlmon_xmit"],
            )
            self.assertEqual(
                abstraction_payload["mvp_scope"]["deferred_callbacks"],
                ["nlmon_get_stats64", "always_on"],
            )

            priorities = {
                entry["area"]: entry["priority"] for entry in abstraction_payload["abstraction_areas"]
            }
            self.assertEqual(priorities["rtnl"], "mvp-blocker")
            self.assertEqual(priorities["netdevice"], "mvp-blocker")
            self.assertEqual(priorities["netlink_tap"], "mvp-blocker")
            self.assertEqual(priorities["stats"], "mvp-blocker")
            self.assertEqual(priorities["skbuff"], "mvp-blocker")
            self.assertEqual(
                abstraction_payload["source_inventory"]["open_tap_assignments"],
                [
                    {
                        "private_binding": "priv",
                        "source_field": "nt",
                        "field": "dev",
                        "operator": "=",
                        "value": "dev",
                    },
                    {
                        "private_binding": "priv",
                        "source_field": "nt",
                        "field": "module",
                        "operator": "=",
                        "value": "THIS_MODULE",
                    },
                ],
            )

            obligation_ids = {entry["id"] for entry in unsafe_payload["obligations"]}
            self.assertIn("netdev-private-layout", obligation_ids)
            self.assertIn("netdev-private-zero-init", obligation_ids)
            self.assertIn("skb-consumed-once", obligation_ids)
            obligation_phase = {entry["id"]: entry["phase"] for entry in unsafe_payload["obligations"]}
            self.assertEqual(obligation_phase["skb-consumed-once"], "mvp")

    def test_patch_plans_report_already_applied_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_realized_mvp_state(root)

            kbuild_patch = intake.build_kbuild_patch_plan("drivers/net/nlmon.c", repo_root=root)
            bindings_patch = intake.build_bindings_patch_plan("drivers/net/nlmon.c", repo_root=root)
            helpers_patch = intake.build_helpers_patch_plan("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(kbuild_patch["status"], "already_applied")
            self.assertFalse(kbuild_patch["patch_required"])
            self.assertEqual(kbuild_patch["patch_units"], [])

            self.assertEqual(bindings_patch["status"], "already_applied")
            self.assertFalse(bindings_patch["patch_required"])
            self.assertEqual(bindings_patch["patch_units"], [])

            self.assertEqual(helpers_patch["status"], "already_applied")
            self.assertFalse(helpers_patch["patch_required"])
            self.assertEqual(helpers_patch["patch_units"], [])

    def test_abstraction_plan_tracks_realized_mvp_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_realized_mvp_state(root)

            abstraction_payload = intake.build_abstraction_plan("drivers/net/nlmon.c", repo_root=root)

            statuses = {entry["area"]: entry["status"] for entry in abstraction_payload["abstraction_areas"]}
            self.assertEqual(statuses["rtnl"], "implemented")
            self.assertEqual(statuses["netdevice"], "implemented")
            self.assertEqual(statuses["netlink_tap"], "implemented")
            self.assertEqual(statuses["stats"], "implemented")
            self.assertEqual(statuses["skbuff"], "implemented")
            self.assertTrue(abstraction_payload["phase_4_readiness"]["ready_for_minimal_driver_codegen"])
            self.assertIn("full smoke-path link-type abstractions", abstraction_payload["current_rust_net_scope"]["assessment"])

    def test_translation_plan_constrains_minimal_driver_loop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_realized_mvp_state(root)

            payload = intake.build_translation_plan("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(payload["artifact_type"], "translation-plan")
            self.assertTrue(payload["readiness"]["ready_for_minimal_driver_codegen"])
            self.assertEqual(payload["driver_rust_path"], "drivers/net/nlmon_rust.rs")
            callback_status = {entry["c_symbol"]: entry["status"] for entry in payload["callback_mapping"]}
            self.assertEqual(callback_status["nlmon_setup"], "required")
            self.assertEqual(callback_status["nlmon_validate"], "required")
            self.assertEqual(callback_status["nlmon_open"], "required")
            self.assertEqual(callback_status["nlmon_close"], "required")
            self.assertEqual(callback_status["nlmon_xmit"], "required")
            self.assertIn("bindings::rtnl_link_register", payload["forbidden_driver_calls"])
            self.assertIn("&THIS_MODULE", payload["allowed_driver_surfaces"])
            self.assertIn("kernel::net::rtnl::{Registration, Driver, ValidateContext, LinkAttr}", payload["allowed_driver_surfaces"])
            self.assertIn("kernel::net::skbuff::SkBuff", payload["allowed_driver_surfaces"])
            self.assertIn("kernel::net::stats", payload["allowed_driver_surfaces"])
            self.assertEqual(
                payload["private_state"]["init_policy"]["allocation_source"],
                "RTNL/net core zero-initialized private storage",
            )
            self.assertEqual(payload["module_shell"]["module_aliases"], ["rtnl-link-nlmon"])

    def test_safety_policy_and_discharge_artifacts_capture_driver_and_abstraction_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)

            policy = intake.build_safety_policy("drivers/net/nlmon.c", repo_root=root)
            discharge = intake.build_soundness_discharge("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(policy["artifact_type"], "safety-policy")
            self.assertTrue(policy["driver_policy"]["zero_unsafe"])
            self.assertTrue(policy["driver_policy"]["zero_bindings"])
            self.assertIn("rust/kernel/net/rtnl.rs", policy["abstraction_policy"]["allowlisted_files"])

            self.assertEqual(discharge["artifact_type"], "soundness-discharge")
            structural_status = {entry["id"]: entry["status"] for entry in discharge["structural_rules"]}
            self.assertEqual(structural_status["typed-link-attr-api"], "discharged")
            self.assertEqual(structural_status["pinned-netlink-tap-api"], "discharged")
            self.assertEqual(structural_status["shared-xmit-device-api"], "discharged")
            self.assertEqual(structural_status["move-only-skb-api"], "discharged")
            self.assertEqual(structural_status["shared-device-stats-api"], "discharged")
            self.assertEqual(structural_status["driver-forbid-unsafe-code-attr"], "discharged")
            driver_status = {entry["id"]: entry["status"] for entry in discharge["driver_rules"]}
            self.assertEqual(driver_status["driver-zero-unsafe"], "discharged")
            self.assertEqual(driver_status["driver-zero-bindings"], "discharged")

    def test_find_rust_unsafe_sites_captures_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "rust" / "kernel"
            target.mkdir(parents=True, exist_ok=True)
            (target / "miscdevice.rs").write_text(
                "impl MiscdeviceVTable {\n"
                "    // SAFETY: private_data stays valid until release.\n"
                "    unsafe extern \"C\" fn open(file: *mut bindings::file) {\n"
                "        let private = unsafe { (*file).private_data };\n"
                "        let _ = private;\n"
                "    }\n"
                "}\n"
            )

            sites = intake._find_rust_unsafe_sites(root, ["rust/kernel/miscdevice.rs"])
            self.assertGreaterEqual(len(sites), 2)
            self.assertEqual(sites[0]["unsafe_kind"], "unsafe-fn")
            self.assertIn("fn open", sites[0]["enclosing_item"])
            self.assertIn("impl MiscdeviceVTable", sites[0]["enclosing_impl_type"])
            self.assertIn("private_data stays valid", sites[0]["safety_comment_window"])
            self.assertIn("unsafe extern \"C\" fn open", sites[0]["source_window"])

    def test_match_unsafe_obligation_ids_uses_contextual_matchers(self):
        profile = {
            "analysis": {
                "unsafe_site_matchers": [
                    {
                        "id": "misc-open-private-data",
                        "file_suffix": "rust/kernel/miscdevice.rs",
                        "unsafe_kind": "unsafe-block",
                        "enclosing_item_contains_any": ["fn open"],
                        "enclosing_impl_contains_any": ["MiscdeviceVTable"],
                        "safety_comment_contains_any": ["private_data stays valid"],
                        "source_window_contains_any": ["private_data"],
                        "obligation_ids": ["miscdevice-file-private-data-lifecycle"],
                    }
                ]
            }
        }
        site = {
            "file": "rust/kernel/miscdevice.rs",
            "unsafe_kind": "unsafe-block",
            "source_excerpt": "let private = unsafe { (*file).private_data };",
            "enclosing_item": "unsafe extern \"C\" fn open(file: *mut bindings::file) {",
            "enclosing_impl_type": "impl MiscdeviceVTable",
            "safety_comment_window": "// SAFETY: private_data stays valid until release.",
            "source_window": "let private = unsafe { (*file).private_data };",
        }

        self.assertEqual(
            intake._match_unsafe_obligation_ids(profile, site),
            ["miscdevice-file-private-data-lifecycle"],
        )

    def test_verify_safety_rejects_driver_unsafe_and_bindings(self):
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for verify-safety")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root, driver_unsafe=True, driver_bindings=True)
            verdict_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "safety-verdict.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "verify-safety",
                    "--repo-root",
                    str(root),
                    "--output",
                    str(verdict_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["pass"])
            violation_kinds = {entry["kind"] for entry in payload["violations"]}
            self.assertIn("driver-forbidden-token", violation_kinds)
            ledger_path = Path(payload["violation_ledger_artifact"])
            self.assertTrue(ledger_path.exists())
            ledger = json.loads(ledger_path.read_text())
            self.assertEqual(ledger["artifact_type"], "safety-violation-ledger")
            self.assertGreaterEqual(ledger["summary"]["by_owner_scope"]["driver"], 1)
            self.assertGreaterEqual(ledger["summary"]["by_classification"]["real_unsoundness_risk"], 1)

    def test_verify_safety_accepts_hardened_state(self):
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for verify-safety")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)
            verdict_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "safety-verdict.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "verify-safety",
                    "--repo-root",
                    str(root),
                    "--output",
                    str(verdict_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["pass"], payload["violations"])
            ledger_path = Path(payload["violation_ledger_artifact"])
            self.assertTrue(ledger_path.exists())
            ledger = json.loads(ledger_path.read_text())
            self.assertEqual(ledger["summary"]["total_violations"], 0)

    def test_verify_safety_accepts_goldfish_macro_unsafe_blocks(self):
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for verify-safety")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)
            (root / "drivers" / "platform" / "goldfish" / "goldfish_address_space.rs").write_text(
                "#![forbid(unsafe_code)]\n\npub struct GoldfishAddressSpaceDriver;\n"
            )
            (root / "rust" / "kernel" / "pci" / "io.rs").write_text(
                "macro_rules! call_config_read {\n"
                "    ($c_fn:ident, $self:ident, $addr:expr, $val:ident) => {{\n"
                "        // SAFETY: the wrapped PCI config helper uses a validated device pointer.\n"
                "        let _ret = unsafe { bindings::$c_fn($self.pdev.as_raw(), $addr as i32, &mut $val) };\n"
                "    }};\n"
                "}\n\n"
                "macro_rules! call_config_write {\n"
                "    ($c_fn:ident, $self:ident, $addr:expr, $value:expr) => {{\n"
                "        // SAFETY: the wrapped PCI config helper uses a validated device pointer.\n"
                "        let _ret = unsafe { bindings::$c_fn($self.pdev.as_raw(), $addr as i32, $value) };\n"
                "    }};\n"
                "}\n\n"
                "pub struct SharedMemoryBar;\n"
                "impl SharedMemoryBar {\n"
                "    pub fn phys_start(&self) -> bindings::resource_size_t { 0 }\n"
                "    pub fn map_into_vma(&self, vma: &VmaNew) -> Result {\n"
                "        let _ = vma;\n"
                "        let _ = (bindings::memremap, bindings::memunmap);\n"
                "        Ok(())\n"
                "    }\n"
                "}\n"
            )

            verdict_path = root / "Documentation" / "rust" / "c2saferust" / "goldfish_address_space" / "safety-verdict.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "verify-safety",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "goldfish_address_space",
                    "--source-tree",
                    str(source_root),
                    "--output",
                    str(verdict_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["pass"], payload["violations"])
            self.assertEqual(payload["summary"]["total_violations"], 0)

    def test_build_safety_violation_ledger_classifies_goldfish_style_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            verdict_path = root / "safety-verdict.json"
            ledger_path = root / "safety-violation-ledger.json"
            verdict_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "safety-verdict",
                        "module_id": "goldfish_address_space",
                        "driver_rust_path": "drivers/platform/goldfish/goldfish_address_space.rs",
                        "pass": False,
                        "violations": [
                            {
                                "kind": "unaccounted-unsafe-site",
                                "file": "rust/kernel/miscdevice.rs",
                                "line": 236,
                                "message": "unsafe site is not covered by soundness-discharge.json",
                            },
                            {
                                "kind": "stale-proof-site",
                                "file": "rust/kernel/miscdevice.rs",
                                "line": 236,
                                "message": "soundness-discharge.json contains a stale unsafe site entry",
                            },
                        ],
                    }
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-safety-violation-ledger",
                    "--verdict",
                    str(verdict_path),
                    "--output",
                    str(ledger_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["artifact_type"], "safety-violation-ledger")
            self.assertEqual(payload["summary"]["by_classification"]["artifact_drift"], 2)
            self.assertEqual(payload["summary"]["by_owner_scope"]["abstraction"], 2)
            self.assertEqual(payload["summary"]["unclassified"], 0)
            self.assertTrue(ledger_path.exists())

    def test_agent_workflow_plan_constrains_agent_io_and_gates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)

            payload = intake.build_agent_workflow_plan("drivers/net/nlmon.c", repo_root=root)

            self.assertEqual(payload["artifact_type"], "agent-workflow-plan")
            self.assertTrue(payload["preflight"]["ready_for_agent_codegen"])
            self.assertIn(
                "Documentation/rust/c2saferust/nlmon/translation-plan.json",
                payload["preflight"]["required_reads"],
            )
            self.assertIn("drivers/net/nlmon_rust.rs", payload["generation_scope"]["managed_files"])
            self.assertIn(
                "#![forbid(unsafe_code)]",
                payload["generation_scope"]["required_driver_crate_attributes"],
            )
            gate_ids = [entry["id"] for entry in payload["acceptance_gates"]]
            self.assertEqual(
                gate_ids,
                ["verify-safety", "rust-toolchain-available", "compile-driver-object", "package-module"],
            )

    def test_oracle_runner_uses_profile_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)

            translation = intake.build_translation_plan("drivers/net/nlmon.c", repo_root=root)
            module_profile = smoke.profiles.load_module_profile("drivers/net/nlmon.c")
            rendered = smoke._render_scenario_inputs("drivers/net/nlmon.c", module_profile, translation)
            self.assertEqual(rendered["link_kind"], "nlmon")
            self.assertEqual(rendered["device_name"], "nlmon0")

    def test_oracle_runner_registry_exposes_qemu_runner(self):
        self.assertIn("qemu-scenario", oracle_runners.available_runner_ids())
        self.assertIn("qemu-module-lifecycle", oracle_runners.available_runner_ids())
        self.assertIn("android-goldfish-oracle", oracle_runners.available_runner_ids())
        self.assertIn("android-emulator-goldfish", oracle_runners.available_runner_ids())
        self.assertIn("configfs-lifecycle-calibration", oracle_runners.available_runner_ids())

    def test_refresh_artifacts_supports_rnull_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_rnull_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "rnull"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "rnull",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["module_id"], "rnull")
            self.assertIn(
                "Documentation/rust/c2saferust/rnull/agent-workflow-plan.json",
                payload["generated_artifacts"],
            )

            abstraction = json.loads((output_dir / "abstraction-plan.json").read_text())
            workflow_plan = json.loads((output_dir / "agent-workflow-plan.json").read_text())
            self.assertEqual(
                abstraction["source_inventory"]["blk_mq_ops"]["field_map"]["queue_rq"],
                "null_queue_rq",
            )
            self.assertIn("blocksize", abstraction["source_inventory"]["configfs"]["device_attributes"])
            self.assertEqual(workflow_plan["driver_object_path"], "drivers/block/rnull/rnull_mod.o")
            self.assertEqual(workflow_plan["driver_module_path"], "drivers/block/rnull/rnull_mod.ko")

    def test_refresh_artifacts_supports_dummy_ast_eval_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "dummy_ast_eval"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "dummy_ast_eval",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["module_id"], "dummy")
            self.assertIn(
                "Documentation/rust/c2saferust/dummy_ast_eval/abstraction-plan.json",
                payload["generated_artifacts"],
            )

            abstraction = json.loads((output_dir / "abstraction-plan.json").read_text())
            source_inventory = abstraction["source_inventory"]
            self.assertEqual(source_inventory["rtnl_link_ops"]["field_map"]["setup"], "dummy_setup")
            self.assertEqual(source_inventory["rtnl_link_ops"]["field_map"]["validate"], "dummy_validate")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_init"], "dummy_dev_init")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_start_xmit"], "dummy_xmit")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_get_stats64"], "dummy_get_stats64")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_change_carrier"], "dummy_change_carrier")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_validate_addr"], "eth_validate_addr")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_set_rx_mode"], "set_multicast_list")
            self.assertEqual(source_inventory["net_device_ops"]["field_map"]["ndo_set_mac_address"], "eth_mac_addr")
            self.assertEqual(source_inventory["ethtool_ops"]["field_map"]["get_ts_info"], "dummy_get_ts_info")
            self.assertIn(
                {"field": "flags", "operator": "&=", "value": "~IFF_MULTICAST"},
                source_inventory["setup_field_writes"],
            )
            self.assertEqual(
                {(entry["field"], entry["return"]) for entry in source_inventory["validate_checks"]},
                {
                    ("IFLA_ADDRESS", "-EINVAL"),
                    ("IFLA_ADDRESS", "-EADDRNOTAVAIL"),
                },
            )
            self.assertIn("dev_lstats_add", source_inventory["xmit_calls"])
            self.assertIn("skb_tx_timestamp", source_inventory["xmit_calls"])
            self.assertIn("dev_kfree_skb", source_inventory["xmit_calls"])
            self.assertIn("dev_lstats_add", source_inventory["callback_calls"]["ndo_start_xmit"])

    def test_auto_process_detects_dummy_known_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "dummy_auto"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "auto-process",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/net/dummy.c",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["artifact_type"], "auto-process-manifest")
            self.assertEqual(manifest["selected_family_id"], "net-link-type")
            self.assertEqual(manifest["selection_mode"], "known-family")
            self.assertIn("Documentation/rust/c2saferust/dummy_auto/auto-profile.json", manifest["generated_artifacts"])
            self.assertIn("Documentation/rust/c2saferust/dummy_auto/abstraction-plan.json", manifest["generated_artifacts"])

            report = json.loads((output_dir / "auto-profile-report.json").read_text())
            abstraction = json.loads((output_dir / "abstraction-plan.json").read_text())
            self.assertIn("dummy_init_module", report["entry_functions"])
            self.assertTrue(
                {"rtnl_link_ops", "net_device_ops"}.issubset(
                    {entry["type_name"] for entry in report["candidate_tables"]}
                )
            )
            required_fields = {entry["field"] for entry in report["required_callback_fields"]}
            self.assertTrue({"setup", "validate", "ndo_start_xmit"}.issubset(required_fields))
            self.assertEqual(
                abstraction["source_inventory"]["rtnl_link_ops"]["field_map"]["setup"],
                "dummy_setup",
            )
            self.assertEqual(
                abstraction["source_inventory"]["net_device_ops"]["field_map"]["ndo_start_xmit"],
                "dummy_xmit",
            )

    def test_auto_process_falls_back_on_unknown_watchdog_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._build_watchdog_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "softdog_auto"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "auto-process",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/watchdog/softdog.c",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["selected_family_id"], "generic-unknown")
            self.assertEqual(manifest["selection_mode"], "generic-unknown")

            report = json.loads((output_dir / "auto-profile-report.json").read_text())
            abstraction = json.loads((output_dir / "abstraction-plan.json").read_text())
            self.assertIn("softdog_init", report["entry_functions"])
            self.assertIn("softdog_ops", {entry["name"] for entry in report["candidate_tables"]})
            self.assertEqual(
                {entry["field"] for entry in report["required_callback_fields"]},
                {"start", "stop"},
            )
            self.assertIn("softdog_ops", abstraction["source_inventory"]["callback_tables"])
            self.assertIn("start", abstraction["source_inventory"]["callback_calls"])
            self.assertIn("stop", abstraction["source_inventory"]["callback_calls"])

    def test_configfs_lifecycle_runner_reports_blocked_when_subsystem_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_rnull_sample_root(root)

            context = intake.resolve_module_context(repo_root=root, profile_id="rnull")
            translation = intake.build_translation_plan(repo_root=root, profile_id="rnull")
            scenario = smoke.profiles.load_scenario_profile(context["profile"]["oracle"]["scenario_id"])
            scenario_inputs = smoke._render_scenario_inputs(
                context["module_path"],
                context["profile"],
                translation,
            )

            configfs_root = root / "fake-configfs"
            dev_root = root / "fake-dev"
            sys_module_root = root / "fake-sys-module"
            proc_modules_path = root / "fake-proc-modules"
            configfs_root.mkdir(parents=True, exist_ok=True)
            dev_root.mkdir(parents=True, exist_ok=True)
            sys_module_root.mkdir(parents=True, exist_ok=True)
            proc_modules_path.write_text("")

            payload = oracle_runners.run_runner(
                "configfs-lifecycle-calibration",
                repo_root=root,
                module_path=context["module_path"],
                module_profile=context["profile"],
                scenario=scenario,
                translation_plan=translation,
                scenario_inputs=scenario_inputs,
                qemu_log_output=None,
                artifact_root=None,
                build_dir=None,
                make_llvm=None,
                timeout_sec=5,
                runner_context={
                    "require_root": False,
                    "skip_module_load": True,
                    "configfs_root": configfs_root,
                    "dev_root": dev_root,
                    "sys_module_root": sys_module_root,
                    "proc_modules_path": proc_modules_path,
                },
            )

            self.assertTrue(payload["blocked"])
            self.assertEqual(payload["blocker_kind"], "configfs-subsystem-missing")
            self.assertEqual(payload["runner"]["id"], "configfs-lifecycle-calibration")
            self.assertEqual(payload["environment"]["configfs_root"], str(configfs_root))

    def test_bootstrap_benchmark_supports_rnull_calibration_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_rnull_sample_root(root)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "rnull-calibration"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "rnull-calibration",
                    "--output-dir",
                    str(benchmark_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["benchmark_id"], "rnull-calibration")

            manifest = json.loads((benchmark_dir / "module-manifest.json").read_text())
            summary = json.loads((benchmark_dir / "validator-ready-summary.json").read_text())
            self.assertEqual(manifest["profile_id"], "rnull")
            self.assertEqual(manifest["driver_rust_path"], "drivers/block/rnull/rnull.rs")
            self.assertEqual(summary["runtime_assessment"]["primary_runner"], "configfs-lifecycle-calibration")
            self.assertTrue(summary["runtime_assessment"]["primary_runner_available"])

    def test_refresh_artifacts_supports_rnull_blind_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_rnull_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "rnull_blind_strict_v1"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "rnull_blind_strict_v1",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["module_id"], "rnull_blind_strict_v1")
            self.assertIn(
                "Documentation/rust/c2saferust/rnull_blind_strict_v1/agent-workflow-plan.json",
                payload["generated_artifacts"],
            )

            abstraction = json.loads((output_dir / "abstraction-plan.json").read_text())
            workflow_plan = json.loads((output_dir / "agent-workflow-plan.json").read_text())
            translation = json.loads((output_dir / "translation-plan.json").read_text())

            self.assertEqual(
                abstraction["source_inventory"]["blk_mq_ops"]["field_map"]["queue_rq"],
                "null_queue_rq",
            )
            self.assertEqual(
                abstraction["source_inventory"]["blk_mq_ops"]["field_map"]["complete"],
                "null_complete_rq",
            )
            self.assertEqual(
                abstraction["source_inventory"]["configfs_group_operations"]["field_map"]["make_group"],
                "nullb_group_make_group",
            )
            self.assertTrue(
                {"power", "blocksize", "rotational", "size", "irqmode"}.issubset(
                    set(abstraction["source_inventory"]["configfs"]["device_attributes"])
                )
            )
            self.assertEqual(workflow_plan["driver_object_path"], "drivers/block/rnull/rnull_mod.o")
            self.assertEqual(workflow_plan["driver_module_path"], "drivers/block/rnull/rnull_mod.ko")
            self.assertEqual(workflow_plan["agent_workflow"]["topology"]["agent_count"], 3)
            gate_commands = [gate["command"] for gate in workflow_plan["acceptance_gates"]]
            self.assertTrue(
                all("/tmp/c2saferust-rnull-build" in command for command in gate_commands[1:])
            )
            self.assertEqual(translation["driver_rust_path"], "drivers/block/rnull/rnull.rs")
            self.assertIn(
                "#![forbid(unsafe_code)]",
                translation["driver_policy"]["required_crate_attributes"],
            )
            self.assertIn(
                "bindings::blk_mq_requeue_request",
                translation["forbidden_driver_calls"],
            )

    def test_bootstrap_benchmark_supports_rnull_blind_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_rnull_sample_root(root)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "rnull-blind-benchmark"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "rnull-blind-benchmark",
                    "--output-dir",
                    str(benchmark_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["benchmark_id"], "rnull-blind-benchmark")

            manifest = json.loads((benchmark_dir / "module-manifest.json").read_text())
            summary = json.loads((benchmark_dir / "validator-ready-summary.json").read_text())
            self.assertEqual(manifest["profile_id"], "rnull_blind_strict_v1")
            self.assertEqual(
                manifest["blind_policy"]["physical_absence_required_paths"],
                [
                    "drivers/block/rnull/rnull.rs",
                    "drivers/block/rnull/configfs.rs",
                ],
            )
            self.assertEqual(summary["runtime_assessment"]["primary_runner"], "qemu-module-lifecycle")
            self.assertTrue(summary["runtime_assessment"]["primary_runner_available"])

    def test_goldfish_external_source_context_resolves_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            context = intake.resolve_module_context(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            self.assertEqual(context["source_tree_role"], "external-tree")
            self.assertEqual(context["module_id"], "goldfish_address_space")
            self.assertEqual(context["module_c_path"], "goldfish_drivers/goldfish_address_space.c")
            self.assertEqual(context["driver_rust_path"], "drivers/platform/goldfish/goldfish_address_space.rs")
            self.assertEqual(context["kconfig_path"], "drivers/platform/goldfish/Kconfig")

    def test_goldfish_kbuild_plan_uses_add_new_driver_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            payload = intake.build_kbuild_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )
            patch_payload = intake.build_kbuild_patch_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            self.assertEqual(payload["kbuild_mode"], "add-new-driver")
            self.assertEqual(payload["suggested_rust_config_symbol"], "GOLDFISH_ADDRESS_SPACE")
            self.assertEqual(payload["suggested_rust_object"], "goldfish_address_space.o")
            self.assertFalse(payload["current_state"]["current_rust_switch_present"])
            self.assertEqual(patch_payload["kbuild_mode"], "add-new-driver")
            self.assertEqual(
                [unit["operation"] for unit in patch_payload["patch_units"]],
                ["insert_after", "insert_after"],
            )

    def test_goldfish_external_header_plan_classifies_uapi_landing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            payload = intake.build_external_header_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            self.assertEqual(payload["artifact_type"], "external-header-plan")
            self.assertEqual(payload["status"], "pending")
            self.assertEqual(payload["blocking_headers"], ["goldfish/goldfish_address_space.h"])

            plans = {entry["header"]: entry for entry in payload["header_plans"]}
            self.assertEqual(plans["defconfig_test.h"]["status"], "no-landing-required")
            self.assertEqual(plans["defconfig_test.h"]["action"], "ignore_private_header")
            self.assertEqual(plans["goldfish/goldfish_address_space.h"]["action"], "land_uapi_header")
            self.assertEqual(plans["goldfish/goldfish_address_space.h"]["landing"]["path"], "include/uapi/linux/goldfish_address_space.h")
            self.assertIn(
                "struct goldfish_address_space_ping_with_data",
                plans["goldfish/goldfish_address_space.h"]["required_symbols"],
            )
            self.assertFalse(plans["goldfish/goldfish_address_space.h"]["landing"]["exists"])
            self.assertFalse(plans["goldfish/goldfish_address_space.h"]["source_present"])

    def test_goldfish_abstraction_plan_tracks_expanded_mvp_areas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            abstraction_payload = intake.build_abstraction_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )
            translation_payload = intake.build_translation_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            statuses = {entry["area"]: entry["status"] for entry in abstraction_payload["abstraction_areas"]}
            self.assertEqual(statuses["pci"], "implemented")
            self.assertEqual(statuses["miscdevice"], "implemented")
            self.assertEqual(statuses["uaccess"], "implemented")
            self.assertEqual(statuses["page"], "implemented")
            self.assertEqual(statuses["vma_pfn_map"], "implemented")
            self.assertEqual(statuses["shared_bar_map"], "implemented")
            self.assertFalse(translation_payload["readiness"]["ready_for_minimal_driver_codegen"])
            self.assertIn(
                "Resolve external header/UAPI landing before driver codegen: goldfish/goldfish_address_space.h.",
                translation_payload["readiness"]["blockers"],
            )
            self.assertIn("Add the landing Kbuild entries before driver codegen.", translation_payload["readiness"]["blockers"])
            callback_status = {entry["c_symbol"]: entry["status"] for entry in translation_payload["callback_mapping"]}
            self.assertEqual(callback_status["goldfish_address_space_open"], "required")
            self.assertEqual(callback_status["goldfish_address_space_release"], "required")
            self.assertEqual(callback_status["goldfish_address_space_ioctl"], "required")
            self.assertEqual(callback_status["goldfish_address_space_compat_ioctl"], "required")
            self.assertEqual(callback_status["goldfish_address_space_mmap"], "required")

    def test_goldfish_profile_uses_minimal_guest_oracle(self):
        profile = smoke.profiles.load_module_profile("goldfish_drivers/goldfish_address_space.c")
        self.assertEqual(profile["oracle"]["runner_id"], "android-goldfish-oracle")
        self.assertEqual(profile["oracle"]["scenario_id"], "goldfish_minimal_guest_runtime")
        self.assertEqual(profile["oracle"]["differential"]["baseline_mode"], "c-baseline-tree")
        self.assertTrue(profile["oracle"]["differential"]["candidate_runner_context"]["managed_guest"]["enabled"])
        self.assertEqual(
            profile["oracle"]["differential"]["baseline_runner_context"]["managed_guest"]["kernel_source"],
            "build-tree",
        )
        self.assertIn(
            "struct goldfish_address_space_ping_with_data",
            profile["external_header_decisions"]["goldfish/goldfish_address_space.h"]["required_symbols"],
        )
        self.assertEqual(profile["command_semantics"]["commands"][0]["completion_mode"], "status-gated")
        self.assertEqual(profile["command_semantics"]["commands"][2]["completion_mode"], "readback-gated")

    def test_goldfish_command_semantics_artifact_is_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            payload = intake.build_command_semantics(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            self.assertEqual(payload["artifact_type"], "command-semantics")
            self.assertEqual(payload["status"], "configured")
            self.assertEqual(payload["summary"]["total_commands"], 5)
            commands = {entry["id"]: entry for entry in payload["commands"]}
            self.assertEqual(commands["generate-handle"]["completion_mode"], "readback-gated")
            self.assertTrue(commands["destroy-handle"]["release_best_effort"])
            self.assertIn(
                "Self::issue_command_locked(&control, CommandId::TellPingInfoAddr)",
                commands["tell-ping-info-addr"]["verification"]["must_contain"],
            )

    def test_verify_command_semantics_accepts_goldfish_hardened_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)
            self._apply_goldfish_command_semantics_hardened_state(root)
            verdict_path = root / "Documentation" / "rust" / "c2saferust" / "goldfish_address_space" / "command-semantics-verdict.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "verify-command-semantics",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "goldfish_address_space",
                    "--source-tree",
                    str(source_root),
                    "--output",
                    str(verdict_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["pass"], payload["violations"])
            self.assertEqual(payload["summary"]["verified_commands"], 5)

    def test_verify_command_semantics_rejects_status_gate_regression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)
            self._apply_goldfish_command_semantics_hardened_state(root)
            driver_path = root / "drivers" / "platform" / "goldfish" / "goldfish_address_space.rs"
            driver_path.write_text(
                driver_path.read_text().replace(
                    "Self::issue_command_locked(&control, CommandId::GenHandle);",
                    "Self::run_command_locked(&control, CommandId::GenHandle)?;",
                )
            )

            payload = semantics.verify_module_command_semantics(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            self.assertFalse(payload["pass"])
            self.assertEqual(payload["summary"]["total_violations"], 1)
            self.assertEqual(payload["violations"][0]["helper"], "generate_handle")

    def test_goldfish_agent_workflow_plan_includes_command_semantics_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)

            payload = intake.build_agent_workflow_plan(
                repo_root=root,
                profile_id="goldfish_address_space",
                source_tree=source_root,
            )

            gate_ids = [entry["id"] for entry in payload["acceptance_gates"]]
            self.assertEqual(
                gate_ids,
                [
                    "verify-safety",
                    "verify-command-semantics",
                    "rust-toolchain-available",
                    "compile-driver-object",
                    "package-module",
                ],
            )

    def test_gate_agent_candidate_requires_safety_before_compile(self):
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for gate-agent-candidate")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root, driver_unsafe=True)
            gate_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "agent-gate-report.json"
            safety_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "safety-verdict.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "gate-agent-candidate",
                    "--repo-root",
                    str(root),
                    "--output",
                    str(gate_path),
                    "--safety-output",
                    str(safety_path),
                    "--skip-compile",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["pass"])
            self.assertFalse(payload["ready_for_smoke"])
            gate_status = {entry["id"]: entry["status"] for entry in payload["gates"]}
            self.assertEqual(gate_status["verify-safety"], "failed")
            self.assertEqual(gate_status["rust-toolchain-available"], "blocked")
            self.assertEqual(gate_status["compile-driver-object"], "blocked")
            self.assertEqual(gate_status["package-module"], "blocked")

    def test_gate_agent_candidate_can_stop_before_smoke_when_compile_skipped(self):
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for gate-agent-candidate")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)
            gate_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "agent-gate-report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "gate-agent-candidate",
                    "--repo-root",
                    str(root),
                    "--output",
                    str(gate_path),
                    "--skip-compile",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["pass"])
            self.assertFalse(payload["ready_for_smoke"])
            gate_status = {entry["id"]: entry["status"] for entry in payload["gates"]}
            self.assertEqual(gate_status["verify-safety"], "passed")
            self.assertEqual(gate_status["rust-toolchain-available"], "skipped")
            self.assertEqual(gate_status["compile-driver-object"], "skipped")
            self.assertEqual(gate_status["package-module"], "skipped")

    def test_run_oracle_blocks_when_safety_fails(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            self._apply_safety_hardened_state(root)

            context = intake.resolve_module_context("drivers/net/nlmon.c", repo_root=root)
            translation_plan = intake.build_translation_plan("drivers/net/nlmon.c", repo_root=root)
            scenario = smoke.profiles.load_scenario_profile(context["profile"]["oracle"]["scenario_id"])

            with mock.patch("smoke.safety.verify_module_safety", return_value={"pass": False, "violations": [{}], "summary": {"total_violations": 1}}), \
                mock.patch("smoke.oracle_runners.run_runner") as mocked_runner:
                payload = smoke.run_oracle(
                    "drivers/net/nlmon.c",
                    repo_root=root,
                    output=root / "run-record.json",
                )

            self.assertFalse(payload["pass"])
            self.assertTrue(payload["blocked"])
            self.assertEqual(payload["blocker_kind"], "safety-verification-failed")
            self.assertEqual(payload["runner"]["status"], "blocked-before-runner")
            self.assertEqual(payload["evidence_tier"], "env_blocked")
            self.assertEqual(payload["scenario_id"], scenario["scenario_id"])
            self.assertEqual(payload["module_id"], translation_plan["module_id"])
            mocked_runner.assert_not_called()

    def test_run_oracle_blocks_when_command_semantics_fail(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)
            self._apply_goldfish_command_semantics_hardened_state(root)

            with mock.patch(
                "smoke.safety.verify_module_safety",
                return_value={"pass": True, "violations": [], "summary": {"total_violations": 0}},
            ), mock.patch(
                "smoke.semantics.verify_module_command_semantics",
                return_value={"pass": False, "violations": [{}], "summary": {"total_violations": 1}},
            ), mock.patch("smoke.oracle_runners.run_runner") as mocked_runner:
                payload = smoke.run_oracle(
                    repo_root=root,
                    profile_id="goldfish_address_space",
                    source_tree=source_root,
                    output=root / "run-record.json",
                )

            self.assertFalse(payload["pass"])
            self.assertTrue(payload["blocked"])
            self.assertEqual(payload["blocker_kind"], "command-semantics-verification-failed")
            mocked_runner.assert_not_called()

    def test_android_goldfish_runner_reports_environment_blocked(self):
        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")
        from unittest import mock

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": None, "adb": None},
                "env": {},
                "missing_tools": ["emulator", "adb"],
                "missing_env": [],
                "available": False,
            },
        ):
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
            )

        self.assertFalse(payload["pass"])
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["blocker_kind"], "android-emulator-environment-missing")
        self.assertEqual(payload["evidence_tier"], "env_blocked")
        self.assertIn("emulator", payload["environment"]["missing_tools"])
        self.assertIn("adb", payload["environment"]["missing_tools"])

    def test_android_goldfish_runner_reports_guest_tester_blocked(self):
        from unittest import mock

        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"},
                "env": {},
                "missing_tools": [],
                "missing_env": [],
                "available": True,
            },
        ), mock.patch(
            "oracle_runners._adb_command",
            return_value=subprocess.CompletedProcess(
                args=["adb"],
                returncode=0,
                stdout="List of devices attached\nemulator-5554\tdevice transport_id:1\n",
                stderr="",
            ),
        ), mock.patch(
            "oracle_runners._adb_device_command",
            side_effect=[
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="crw-rw-rw- 1 system system 10, 121 /dev/goldfish_address_space\n", stderr=""),
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="OPEN_OK\nCLOSE_OK\n", stderr=""),
            ],
        ), mock.patch(
            "oracle_runners._run_android_guest_tester",
            return_value={
                "status": "blocked",
                "blocker_kind": "android-guest-tester-toolchain-missing",
                "transcript_path": "/tmp/goldfish.log",
                "steps": [
                    {
                        "step_id": "build-android-guest-tester",
                        "status": "blocked",
                        "details": ["missing compiler"],
                    }
                ],
            },
        ):
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
            )

        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["evidence_tier"], "device_present")
        self.assertEqual(payload["blocker_kind"], "android-guest-tester-toolchain-missing")
        self.assertEqual(payload["runner"]["log_path"], "/tmp/goldfish.log")

    def test_android_goldfish_runner_reports_device_presence_with_open_close_smoke(self):
        from unittest import mock

        scenario = smoke.profiles.load_scenario_profile("goldfish_address_space_smoke")

        def fake_completed(stdout: str, returncode: int = 0, stderr: str = ""):
            return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"},
                "env": {"C2SAFERUST_ANDROID_AVD": "gf_x86_64"},
                "missing_tools": [],
                "missing_env": [],
                "available": True,
            },
        ), mock.patch(
            "oracle_runners._adb_command",
            return_value=fake_completed("List of devices attached\nemulator-5554\tdevice transport_id:1\n"),
        ), mock.patch(
            "oracle_runners._adb_device_command",
            side_effect=[
                fake_completed("crw-rw-rw- 1 system system 10, 121 /dev/goldfish_address_space\n"),
                fake_completed("OPEN_OK\nCLOSE_OK\n"),
            ],
        ):
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
            )

        self.assertFalse(payload["pass"])
        self.assertFalse(payload["blocked"])
        self.assertTrue(payload["ready_for_oracle"])
        self.assertEqual(payload["evidence_tier"], "device_present")
        self.assertEqual(payload["runner"]["status"], "partial")
        self.assertEqual(payload["smoke_summary"]["open_close_smoke"], "PASS")

    def test_android_goldfish_runner_reports_baseline_abi_pass_with_guest_tester(self):
        from unittest import mock

        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"},
                "env": {},
                "missing_tools": [],
                "missing_env": [],
                "available": True,
            },
        ), mock.patch(
            "oracle_runners._adb_command",
            return_value=subprocess.CompletedProcess(
                args=["adb"],
                returncode=0,
                stdout="List of devices attached\nemulator-5554\tdevice transport_id:1\n",
                stderr="",
            ),
        ), mock.patch(
            "oracle_runners._adb_device_command",
            side_effect=[
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="crw-rw-rw- 1 system system 10, 121 /dev/goldfish_address_space\n", stderr=""),
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="OPEN_OK\nCLOSE_OK\n", stderr=""),
            ],
        ), mock.patch(
            "oracle_runners._run_android_guest_tester",
            return_value={
                "status": "pass",
                "transcript_path": "/tmp/goldfish-baseline.log",
                "summary": {
                    "result": "PASS",
                    "open_rc": 0,
                    "ping_device_type_rc": 0,
                    "allocate_block_rc": 0,
                    "ping_allocate_rc": 0,
                    "mmap_rc": 0,
                    "invalid_mmap_rc": -1,
                    "ping_unallocate_rc": 0,
                    "deallocate_block_rc": 0,
                    "close_rc": 0,
                },
                "build": {"output_path": "/tmp/goldfish_tester"},
                "steps": [
                    {
                        "step_id": "run-android-guest-tester",
                        "status": "pass",
                        "details": ["[goldfish-oracle] RESULT=PASS"],
                    }
                ],
            },
        ):
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
            )

        self.assertFalse(payload["blocked"])
        self.assertTrue(payload["pass"])
        self.assertEqual(payload["evidence_tier"], "baseline_abi_pass")
        self.assertEqual(payload["runner"]["status"], "pass")
        self.assertEqual(payload["runner"]["log_path"], "/tmp/goldfish-baseline.log")
        self.assertEqual(payload["smoke_summary"]["mmap_rc"], 0)

    def test_android_boot_failure_classifier_detects_ramdisk_module_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "init: Failed to insmod '/lib/modules/virtio_blk.ko' with args '': Exec format error",
                        "module virtio_blk: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time",
                        "init: Failed to mount required partitions early ...",
                        "init: InitFatalReboot: signal 6",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-ramdisk-module-abi-mismatch")
        self.assertIn("ramdisk-module-abi-mismatch", payload["markers"])
        self.assertIn("virtio-blk-ramdisk-module-load-failed", payload["markers"])
        self.assertIn("first-stage-mount-failure", payload["markers"])

    def test_android_boot_failure_classifier_detects_dm_verity_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "init: [libfs_avb] Built verity table: '...'",
                        "device-mapper: table: 252:5: verity: unknown target type",
                        "init: [libfs_avb] Couldn't create verity device!",
                        "init: Failed to setup verity for '/system': Invalid argument",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-dm-verity-missing")
        self.assertIn("missing-dm-verity", payload["markers"])

    def test_android_boot_failure_classifier_detects_erofs_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "init: [libfs_mgr] __mount(source=/dev/block/dm-1,target=/system_dlkm,type=erofs)=-1: No such device",
                        "init: Failed to mount /system_dlkm: No such device",
                        "init: Failed to mount required partitions early ...",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-erofs-missing")
        self.assertIn("missing-erofs", payload["markers"])
        self.assertIn("first-stage-mount-failure", payload["markers"])

    def test_android_boot_failure_classifier_detects_binder_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "hw-ProcessState: Opening '/dev/hwbinder' failed: No such file or directory",
                        "DEBUG: Abort message: 'Binder driver '/dev/binder' could not be opened. Terminating: Opening '/dev/binder' failed: No such file or directory'",
                        "init: Service vold has 'reboot_on_failure' option and failed, shutting down system.",
                        "init: Reboot start, reason: reboot,vold-failed, reboot_target: vold-failed",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-binder-missing")
        self.assertIn("missing-binder", payload["markers"])
        self.assertIn("vold-reboot", payload["markers"])

    def test_android_boot_failure_classifier_detects_default_key_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "device-mapper: table: 252:40: default-key: unknown target type",
                        "vold: DM_TABLE_LOAD failed: Invalid argument",
                        "vold: Failed to populate default-key device userdata",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-default-key-missing")
        self.assertIn("missing-default-key", payload["markers"])

    def test_android_boot_failure_classifier_detects_raw_image_contract_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "ERROR        | You did not provide the name of an Android Virtual Device",
                        "with the '-avd <name>' option. Read -help-avd for more information.",
                        "If you *really* want to *NOT* run an AVD, consider using '-data <file>'",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-raw-image-contract-mismatch")
        self.assertIn("raw-image-contract-mismatch", payload["markers"])

    def test_android_boot_failure_classifier_detects_missing_vendor_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "ERROR        | Your system directory is missing the 'vendor.img' image file.\n"
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertEqual(payload["blocker_kind"], "android-emulator-vendor-image-missing")
        self.assertIn("missing-vendor-image", payload["markers"])

    def test_android_boot_failure_classifier_ignores_non_virtio_ramdisk_module_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "android-emulator.log"
            log_path.write_text(
                "\n".join(
                    [
                        "module goldfish_address_space: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time",
                        "dlkm_loader: Failed to insmod '/vendor/lib/modules/goldfish_address_space.ko' with args '': Exec format error",
                    ]
                )
            )

            payload = oracle_runners._classify_android_boot_failure(log_path)

        self.assertNotEqual(payload["blocker_kind"], "android-emulator-ramdisk-module-abi-mismatch")
        self.assertIn("second-stage-module-abi-mismatch", payload["markers"])

    def test_prepare_managed_guest_ramdisk_patches_concatenated_cpio(self):
        archive_one = [
            {
                "name": "init",
                "mode": 0o100755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"elf",
            },
            {
                "name": "TRAILER!!!",
                "mode": 0o755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
        ]
        archive_two = [
            {
                "name": "lib",
                "mode": 0o040755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
            {
                "name": "lib/modules",
                "mode": 0o040755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
            {
                "name": "lib/modules/modules.load",
                "mode": 0o100644,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"virtio_blk.ko\nvirtio_console.ko\n",
            },
            {
                "name": "first_stage_ramdisk",
                "mode": 0o040755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
            {
                "name": "first_stage_ramdisk/fstab.ranchu",
                "mode": 0o100644,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"system /system ext4 ro wait,logical,first_stage_mount\n",
            },
            {
                "name": "TRAILER!!!",
                "mode": 0o755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            ramdisk_path = repo_root / "stock.cpio"
            ramdisk_path.write_bytes(
                oracle_runners._write_concatenated_newc_archives([archive_one, archive_two])
            )

            result = oracle_runners._prepare_managed_guest_ramdisk(
                repo_root=repo_root,
                module_id="goldfish_address_space",
                managed_guest={
                    "label": "candidate",
                    "ramdisk_patch": {
                        "enabled": True,
                        "artifact_filename": "patched.cpio",
                        "mutations": [
                            {
                                "action": "write_text",
                                "archive_index": 2,
                                "path": "lib/modules/modules.load",
                                "text": "",
                            }
                        ],
                    },
                },
                ramdisk_path=ramdisk_path,
            )

            self.assertEqual(result["status"], "pass")
            patched_payload = Path(result["path"]).read_bytes()
            archives = oracle_runners._parse_concatenated_newc_archives(patched_payload)
            self.assertEqual(len(archives), 2)
            entry = oracle_runners._find_archive_entry(archives, "lib/modules/modules.load", archive_index=2)
            self.assertIsNotNone(entry)
            self.assertEqual(entry[2]["data"], b"")
            self.assertEqual(result["operations"][0]["archive_index"], 2)

    def test_prepare_managed_guest_image_patches_updates_partitioned_vendor_image(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            vendor_image = repo_root / "vendor.img"
            vendor_image.write_bytes(b"vendor-image")

            def fake_extract(image_path, *, partition, output_path):
                self.assertEqual(image_path, repo_root / "Documentation" / "rust" / "c2saferust" / "goldfish_address_space" / "patched-vendor.img")
                self.assertEqual(partition["index"], 1)
                output_path.write_bytes(b"partition-image")

            with mock.patch(
                "oracle_runners._read_disk_partitions",
                return_value={
                    "sector_size": 512,
                    "partitions": [
                        {
                            "index": 1,
                            "start_sector": 2048,
                            "end_sector": 200703,
                            "sector_count": 198656,
                            "sector_size": 512,
                            "fs_type": "ext4",
                            "name": "vendor",
                        }
                    ],
                },
            ), mock.patch(
                "oracle_runners._extract_disk_partition",
                side_effect=fake_extract,
            ) as mocked_extract, mock.patch(
                "oracle_runners._apply_ext_filesystem_patch",
                return_value=[
                    {
                        "index": 0,
                        "action": "write_text",
                        "path": "etc/fstab.ranchu",
                        "status": "updated",
                        "size": 12,
                    }
                ],
            ) as mocked_apply, mock.patch(
                "oracle_runners._replace_disk_partition"
            ) as mocked_replace:
                result = oracle_runners._prepare_managed_guest_image_patches(
                    repo_root=repo_root,
                    module_id="goldfish_address_space",
                    managed_guest={
                        "label": "candidate",
                        "image_patches": [
                            {
                                "asset": "vendor",
                                "artifact_filename": "patched-vendor.img",
                                "partition_index": 1,
                                "filesystem": "ext4",
                                "mutations": [
                                    {
                                        "action": "write_text",
                                        "path": "/etc/fstab.ranchu",
                                        "text": "patched",
                                    }
                                ],
                            }
                        ],
                    },
                    assets={
                        "vendor": str(vendor_image),
                    },
                )

            self.assertEqual(result["status"], "pass")
            self.assertTrue(result["patched"])
            patched_vendor = repo_root / "Documentation" / "rust" / "c2saferust" / "goldfish_address_space" / "patched-vendor.img"
            self.assertEqual(result["assets"]["vendor"], str(patched_vendor))
            self.assertEqual(result["operations"][0]["asset"], "vendor")
            self.assertEqual(result["operations"][0]["partition_index"], 1)
            mocked_extract.assert_called_once()
            mocked_apply.assert_called_once()
            mocked_replace.assert_called_once()

    def test_start_managed_android_guest_raw_image_mode_uses_sysdir_and_initdata(self):
        from unittest import mock

        class FakeProcess:
            returncode = None

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            image_dir = repo_root / "android23"
            image_dir.mkdir()
            kernel_path = image_dir / "kernel-ranchu"
            ramdisk_path = image_dir / "ramdisk.img"
            system_path = image_dir / "system.img"
            userdata_path = image_dir / "userdata.img"
            for path in [kernel_path, ramdisk_path, system_path, userdata_path]:
                path.write_bytes(b"image")

            with mock.patch(
                "oracle_runners._wait_for_adb_device",
                return_value={"status": "pass", "details": ["adb-ready"]},
            ), mock.patch(
                "oracle_runners._wait_for_android_boot",
                return_value={"status": "pass", "details": ["boot-ready"]},
            ), mock.patch(
                "oracle_runners._synthesize_ext4_image",
                side_effect=lambda path, **kwargs: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(b"synthetic-vendor")),
            ), mock.patch(
                "oracle_runners.shutil.copy2"
            ) as mocked_copy, mock.patch(
                "oracle_runners.subprocess.Popen",
                return_value=FakeProcess(),
            ) as mocked_popen:
                result = oracle_runners._start_managed_android_guest(
                    repo_root=repo_root,
                    module_id="goldfish_address_space",
                    scenario={},
                    env_report={"tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"}},
                    managed_guest={
                        "label": "baseline23",
                        "kernel_source": "sdk-stock",
                        "serial_port_base": 5564,
                        "image_dir": str(image_dir),
                        "avd_name": None,
                    },
                )

            command = mocked_popen.call_args.args[0]
            popen_env = mocked_popen.call_args.kwargs["env"]
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["launch_mode"], "raw-image")
            self.assertNotIn("@gf_x86_64", command)
            self.assertIn("-sysdir", command)
            self.assertEqual(command[command.index("-sysdir") + 1], str(image_dir))
            self.assertIn("-initdata", command)
            self.assertEqual(command[command.index("-initdata") + 1], str(userdata_path))
            self.assertIn("-data", command)
            self.assertTrue(command[command.index("-data") + 1].endswith("userdata-qemu.img"))
            self.assertIn("-vendor", command)
            self.assertEqual(command[command.index("-vendor") + 1], result["assets"]["vendor"])
            self.assertEqual(popen_env["ANDROID_PRODUCT_OUT"], str(image_dir))
            self.assertIn("vendor", result["synthetic_assets"])
            mocked_copy.assert_not_called()
            shutil.rmtree(result["state_dir"], ignore_errors=True)

    def test_start_managed_android_guest_minimal_linux_uses_qemu_append_and_synthetic_images(self):
        from unittest import mock

        class FakeProcess:
            returncode = None

            def poll(self):
                return None

        scenario = smoke.profiles.load_scenario_profile("goldfish_minimal_guest_runtime")

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            build_dir = repo_root / "build"
            (build_dir / "arch" / "x86" / "boot").mkdir(parents=True, exist_ok=True)
            kernel_image = build_dir / "arch" / "x86" / "boot" / "bzImage"
            kernel_image.write_bytes(b"kernel")
            tester_output = repo_root / "goldfish_address_space_tester"
            tester_output.write_bytes(b"tester")
            tester_output.chmod(0o755)
            ramdisk_temp = repo_root / "minimal.cpio"
            ramdisk_temp.write_bytes(b"cpio")

            def fake_synthesize(path, **kwargs):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ext4-image")

            with mock.patch(
                "oracle_runners._build_guest_tester",
                return_value={
                    "status": "pass",
                    "output_path": str(tester_output),
                    "source_path": str(REPO_ROOT / "scripts" / "c2saferust" / "oracles" / "goldfish_address_space_tester.c"),
                },
            ), mock.patch(
                "oracle_runners._build_initramfs",
                return_value=(
                    ramdisk_temp,
                    {
                        "rootfs_dir": str(repo_root / "rootfs"),
                        "initramfs_path": str(ramdisk_temp),
                    },
                ),
            ), mock.patch(
                "oracle_runners._synthesize_ext4_image",
                side_effect=fake_synthesize,
            ), mock.patch(
                "oracle_runners.subprocess.Popen",
                return_value=FakeProcess(),
            ) as mocked_popen:
                result = oracle_runners._start_managed_android_guest(
                    repo_root=repo_root,
                    module_id="goldfish_address_space",
                    scenario=scenario,
                    scenario_inputs={"device_node": "/dev/goldfish_address_space"},
                    env_report={"tool_paths": {"emulator": "/tmp/emulator"}},
                    managed_guest={
                        "enabled": True,
                        "boot_mode": "minimal-linux",
                        "label": "candidate",
                        "kernel_source": "build-tree",
                        "serial_port_base": 5560,
                    },
                    build_dir=build_dir,
                )

            command = mocked_popen.call_args.args[0]
            popen_env = mocked_popen.call_args.kwargs["env"]
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["launch_mode"], "minimal-linux")
            self.assertIn("-sysdir", command)
            self.assertIn("-initdata", command)
            self.assertIn("-data", command)
            self.assertIn("-qemu", command)
            self.assertIn("-append", command)
            self.assertEqual(command[command.index("-kernel") + 1], str(kernel_image))
            self.assertEqual(command[command.index("-ramdisk") + 1], result["assets"]["ramdisk"])
            self.assertEqual(command[command.index("-system") + 1], result["assets"]["system"])
            self.assertEqual(command[command.index("-vendor") + 1], result["assets"]["vendor"])
            self.assertEqual(command[command.index("-initdata") + 1], result["assets"]["userdata"])
            self.assertEqual(
                command[command.index("-append") + 1],
                "console=ttyS0 rdinit=/init nokaslr panic=-1",
            )
            self.assertEqual(popen_env["ANDROID_PRODUCT_OUT"], result["assets"]["image_dir"])
            self.assertEqual(result["guest_tester"]["output_path"], str(tester_output))
            self.assertEqual(result["initramfs"]["artifact_path"], result["assets"]["ramdisk"])
            shutil.rmtree(result["state_dir"], ignore_errors=True)

    def test_resolve_managed_guest_config_prefers_gpu_off_for_raw_image(self):
        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")

        resolved = oracle_runners._resolve_managed_guest_config(
            scenario,
            {
                "managed_guest": {
                    "enabled": True,
                    "avd_name": None,
                    "image_dir": "/tmp/android23",
                }
            },
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved["emulator_args"],
            [
                "-no-window",
                "-no-snapshot-load",
                "-no-snapshot-save",
                "-wipe-data",
                "-gpu",
                "off",
                "-show-kernel",
            ],
        )

    def test_android_goldfish_runner_supports_managed_guest_boot(self):
        from unittest import mock

        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"},
                "env": {},
                "missing_tools": [],
                "missing_env": [],
                "available": True,
            },
        ), mock.patch(
            "oracle_runners._start_managed_android_guest",
            return_value={
                "status": "pass",
                "serial": "emulator-5560",
                "log_path": "/tmp/android-emulator-candidate.log",
                "command": ["/tmp/emulator", "@gf_x86_64"],
                "kernel_source": "build-tree",
                "steps": [
                    {
                        "step_id": "launch-managed-android-emulator",
                        "status": "pass",
                        "details": ["serial=emulator-5560"],
                    }
                ],
            },
        ), mock.patch(
            "oracle_runners._adb_device_command",
            side_effect=[
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="crw-rw-rw- 1 system system 10, 121 /dev/goldfish_address_space\n", stderr=""),
                subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="OPEN_OK\nCLOSE_OK\n", stderr=""),
            ],
        ), mock.patch(
            "oracle_runners._run_android_guest_tester",
            return_value={
                "status": "pass",
                "transcript_path": "/tmp/goldfish-managed.log",
                "summary": {
                    "result": "PASS",
                    "open_rc": 0,
                    "ping_device_type_rc": 0,
                    "allocate_block_rc": 0,
                    "ping_allocate_rc": 0,
                    "mmap_rc": 0,
                    "invalid_mmap_rc": -1,
                    "ping_unallocate_rc": 0,
                    "deallocate_block_rc": 0,
                    "close_rc": 0,
                },
                "build": {"output_path": "/tmp/goldfish_tester"},
                "steps": [
                    {
                        "step_id": "run-android-guest-tester",
                        "status": "pass",
                        "details": ["[goldfish-oracle] RESULT=PASS"],
                    }
                ],
            },
        ), mock.patch(
            "oracle_runners._stop_managed_android_guest",
            return_value={"status": "pass", "details": ["emu-killed"]},
        ) as mocked_stop:
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
                runner_context={
                    "managed_guest": {
                        "enabled": True,
                        "label": "candidate",
                        "kernel_source": "build-tree",
                        "serial_port_base": 5560,
                    }
                },
            )

        self.assertTrue(payload["pass"])
        self.assertEqual(payload["managed_guest"]["serial"], "emulator-5560")
        self.assertEqual(payload["managed_guest"]["kernel_source"], "build-tree")
        mocked_stop.assert_called_once()

    def test_run_minimal_linux_guest_oracle_parses_pass_summary(self):
        class FakeProcess:
            def __init__(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

            def poll(self):
                return 0

        scenario = smoke.profiles.load_scenario_profile("goldfish_minimal_guest_runtime")

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "minimal-guest.log"
            log_path.write_text(
                "\n".join(
                    [
                        "[goldfish-oracle] open_rc=0",
                        "[goldfish-oracle] ping_device_type_rc=0",
                        "[goldfish-oracle] allocate_block_rc=0",
                        "[goldfish-oracle] ping_allocate_rc=0",
                        "[goldfish-oracle] mmap_rc=0",
                        "[goldfish-oracle] invalid_mmap_rc=-1",
                        "[goldfish-oracle] ping_unallocate_rc=0",
                        "[goldfish-oracle] deallocate_block_rc=0",
                        "[goldfish-oracle] close_rc=0",
                        "[goldfish-oracle] RESULT=PASS",
                    ]
                )
            )

            payload = oracle_runners._run_minimal_linux_guest_oracle(
                repo_root=Path(temp_dir),
                module_id="goldfish_address_space",
                scenario=scenario,
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
                managed_guest={"boot_timeout_sec": 30},
                managed_state={"log_path": str(log_path), "process": FakeProcess()},
                timeout_sec=30,
            )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["summary"]["mmap_rc"], 0)
        self.assertEqual(payload["summary"]["invalid_mmap_rc"], -1)

    def test_run_minimal_linux_guest_oracle_reports_device_missing(self):
        class FakeProcess:
            def __init__(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

            def poll(self):
                return 0

        scenario = smoke.profiles.load_scenario_profile("goldfish_minimal_guest_runtime")

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "minimal-guest.log"
            log_path.write_text(
                "\n".join(
                    [
                        "[goldfish-oracle] begin minimal guest oracle",
                        "[goldfish-oracle] DEVICE_NODE_MISSING=1",
                        "[goldfish-oracle] RESULT=FAIL",
                    ]
                )
            )

            payload = oracle_runners._run_minimal_linux_guest_oracle(
                repo_root=Path(temp_dir),
                module_id="goldfish_address_space",
                scenario=scenario,
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
                managed_guest={"boot_timeout_sec": 30},
                managed_state={"log_path": str(log_path), "process": FakeProcess()},
                timeout_sec=30,
            )

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocker_kind"], "minimal-guest-device-missing")

    def test_android_goldfish_runner_cleans_up_failed_managed_guest(self):
        from unittest import mock

        scenario = smoke.profiles.load_scenario_profile("goldfish_android_runtime")

        with mock.patch(
            "oracle_runners._detect_android_emulator_env",
            return_value={
                "tool_paths": {"emulator": "/tmp/emulator", "adb": "/tmp/adb"},
                "env": {},
                "missing_tools": [],
                "missing_env": [],
                "available": True,
            },
        ), mock.patch(
            "oracle_runners._start_managed_android_guest",
            return_value={
                "status": "blocked",
                "blocker_kind": "android-emulator-boot-timeout",
                "details": ["boot-timeout"],
                "serial": "emulator-5560",
                "process": object(),
                "state_dir": "/tmp/managed-state",
                "log_path": "/tmp/android-emulator-candidate.log",
            },
        ), mock.patch(
            "oracle_runners._stop_managed_android_guest",
            return_value={"status": "pass", "details": ["emu-killed"]},
        ) as mocked_stop:
            payload = oracle_runners.run_runner(
                "android-goldfish-oracle",
                repo_root=Path("/tmp"),
                module_path="goldfish_drivers/goldfish_address_space.c",
                module_profile={"profile_id": "goldfish_address_space"},
                scenario=scenario,
                translation_plan={"module_id": "goldfish_address_space"},
                scenario_inputs={"device_node": "/dev/goldfish_address_space"},
                runner_context={
                    "managed_guest": {
                        "enabled": True,
                        "label": "candidate",
                        "kernel_source": "build-tree",
                        "serial_port_base": 5560,
                    }
                },
            )

        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["blocker_kind"], "android-emulator-boot-timeout")
        mocked_stop.assert_called_once()

    def test_run_differential_oracle_skips_baseline_safety_gate(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rust_root = root / "rust-tree"
            c_root = root / "c-tree"
            self._build_sample_root(rust_root)
            self._build_sample_root(c_root)
            rust_source = self._build_external_goldfish_source(rust_root)
            c_source = self._build_external_goldfish_source(c_root)

            runner_payload = {
                "runner": {"id": "android-goldfish-oracle", "status": "blocked"},
                "pass": False,
                "ready_for_oracle": False,
                "blocked": True,
                "evidence_tier": "env_ready",
                "blocker_kind": "android-emulator-environment-missing",
                "blockers": ["missing emulator"],
                "smoke_summary": {"result": "BLOCKED"},
                "steps": [
                    {
                        "step_id": "detect-android-emulator-env",
                        "status": "blocked",
                        "details": ["missing_tool=emulator"],
                    }
                ],
            }

            with mock.patch(
                "smoke.safety.verify_module_safety",
                return_value={"pass": True, "violations": [], "summary": {"total_violations": 0}},
            ) as mocked_verify, mock.patch(
                "smoke.semantics.verify_module_command_semantics",
                return_value={"pass": True, "violations": [], "summary": {"total_violations": 0}},
            ) as mocked_verify_command_semantics, mock.patch(
                "smoke.oracle_runners.run_runner",
                return_value=runner_payload,
            ) as mocked_runner:
                payload = smoke.run_differential_oracle(
                    "goldfish_drivers/goldfish_address_space.c",
                    repo_root=rust_root,
                    profile_id="goldfish_address_space",
                    source_tree=rust_source,
                    baseline_kernel_tree=c_root,
                    baseline_profile_id="goldfish_address_space",
                    baseline_source_tree=c_source,
                    output=rust_root / "diff.json",
                )

            self.assertTrue(payload["blocked"])
            self.assertEqual(payload["evidence_tier"], "env_ready")
            self.assertEqual(payload["candidate"]["evidence_tier"], "env_ready")
            self.assertEqual(payload["baseline"]["evidence_tier"], "env_ready")
            self.assertEqual(
                mocked_runner.call_args_list[0].kwargs["runner_context"],
                smoke.profiles.load_module_profile_by_id("goldfish_address_space")["oracle"]["differential"][
                    "candidate_runner_context"
                ],
            )
            self.assertEqual(
                mocked_runner.call_args_list[1].kwargs["runner_context"],
                smoke.profiles.load_module_profile_by_id("goldfish_address_space")["oracle"]["differential"][
                    "baseline_runner_context"
                ],
            )
            self.assertEqual(mocked_verify.call_count, 1)
            self.assertEqual(mocked_verify_command_semantics.call_count, 1)
            self.assertEqual(mocked_runner.call_count, 2)

    def test_score_candidate_targets_builder_prefers_goldfish_for_now(self):
        payload = candidate_targets.build_candidate_target_scoreboard()

        self.assertEqual(payload["artifact_type"], "candidate-target-scoreboard")
        self.assertEqual(payload["current_primary"], "goldfish_address_space")
        self.assertEqual(payload["recommendation"]["action"], "keep-primary")
        self.assertEqual(payload["recommendation"]["candidate_id"], "goldfish_address_space")
        self.assertGreaterEqual(len(payload["candidates"]), 4)

    def test_score_candidate_targets_cli_writes_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "candidate-target-scoreboard.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "score-candidate-targets",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["artifact_type"], "candidate-target-scoreboard")
            self.assertTrue(output.exists())

    def test_run_oracle_cli_delegates_to_oracle_runner(self):
        from unittest import mock

        args = type(
            "Args",
            (),
            {
                "module_path": "drivers/net/nlmon.c",
                "repo_root": "/tmp/repo",
                "output": "/tmp/out.json",
                "qemu_log_output": "/tmp/log.txt",
                "build_dir": "/tmp/build",
                "make_llvm": "-15",
                "timeout_sec": 99,
            },
        )()

        payload = {"artifact_type": "toolchain-run-record", "module_id": "nlmon"}
        with mock.patch("smoke.run_oracle", return_value=payload) as mocked:
            rendered = tool_cli.run_oracle(args)

        self.assertEqual(json.loads(rendered), payload)
        mocked.assert_called_once_with(
            "drivers/net/nlmon.c",
            repo_root="/tmp/repo",
            artifact_root=None,
            profile_id=None,
            source_tree=None,
            output="/tmp/out.json",
            qemu_log_output="/tmp/log.txt",
            build_dir="/tmp/build",
            make_llvm="-15",
            timeout_sec=99,
        )

    def test_run_differential_oracle_cli_delegates(self):
        from unittest import mock

        args = type(
            "Args",
            (),
            {
                "module_path": "goldfish_drivers/goldfish_address_space.c",
                "repo_root": "/tmp/rust-tree",
                "artifact_root": None,
                "profile_id": "goldfish_address_space",
                "source_tree": "/tmp/source",
                "baseline_kernel_tree": "/tmp/c-tree",
                "baseline_module_path": "goldfish_drivers/goldfish_address_space.c",
                "baseline_profile_id": "goldfish_address_space",
                "baseline_source_tree": "/tmp/source",
                "output": "/tmp/diff.json",
                "build_dir": "/tmp/rust-build",
                "baseline_build_dir": "/tmp/c-build",
                "make_llvm": "-15",
                "timeout_sec": 99,
            },
        )()

        payload = {"artifact_type": "differential-oracle-run-record", "module_id": "goldfish_address_space"}
        with mock.patch("smoke.run_differential_oracle", return_value=payload) as mocked:
            rendered = tool_cli.run_differential_oracle(args)

        self.assertEqual(json.loads(rendered), payload)
        mocked.assert_called_once_with(
            "goldfish_drivers/goldfish_address_space.c",
            repo_root="/tmp/rust-tree",
            artifact_root=None,
            profile_id="goldfish_address_space",
            source_tree="/tmp/source",
            baseline_kernel_tree="/tmp/c-tree",
            baseline_module_path="goldfish_drivers/goldfish_address_space.c",
            baseline_profile_id="goldfish_address_space",
            baseline_source_tree="/tmp/source",
            output="/tmp/diff.json",
            build_dir="/tmp/rust-build",
            baseline_build_dir="/tmp/c-build",
            make_llvm="-15",
            timeout_sec=99,
        )

    def test_run_smoke_qemu_alias_delegates_to_run_oracle(self):
        from unittest import mock

        args = type(
            "Args",
            (),
            {
                "module_path": "drivers/net/nlmon.c",
                "repo_root": "/tmp/repo",
                "output": "/tmp/out.json",
                "qemu_log_output": "/tmp/log.txt",
                "build_dir": "/tmp/build",
                "make_llvm": "-15",
                "timeout_sec": 99,
            },
        )()

        payload = {"artifact_type": "toolchain-run-record", "module_id": "nlmon"}
        with mock.patch.object(tool_cli, "run_oracle", return_value=intake.stable_json(payload)) as mocked:
            rendered = tool_cli.run_smoke_qemu(args)

        self.assertEqual(json.loads(rendered), payload)
        mocked.assert_called_once_with(args)

    def test_bootstrap_module_writes_all_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "nlmon"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-module",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/net/nlmon.c",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            expected_files = {
                "kbuild-plan.json",
                "binding-gap-audit.json",
                "external-header-plan.json",
                "helper-audit.json",
                "kbuild-patch-plan.json",
                "bindings-patch-plan.json",
                "helpers-patch-plan.json",
                "abstraction-plan.json",
                "unsafe-obligations.json",
                "translation-plan.json",
                "command-semantics.json",
                "safety-policy.json",
                "soundness-discharge.json",
                "agent-workflow-plan.json",
            }
            self.assertEqual(expected_files, {path.name for path in output_dir.iterdir()})

            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["artifact_type"], "bootstrap-manifest")
            self.assertTrue(all(not Path(path).is_absolute() for path in manifest["generated_artifacts"]))
            self.assertEqual(
                {
                    "Documentation/rust/c2saferust/nlmon",
                },
                {str(Path(path).parent) for path in manifest["generated_artifacts"]},
            )

    def test_refresh_artifacts_uses_profile_default_output_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/net/nlmon.c",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["module_id"], "nlmon")
            self.assertTrue(all(not Path(path).is_absolute() for path in manifest["generated_artifacts"]))
            self.assertTrue((root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "translation-plan.json").exists())

    def test_refresh_artifacts_accepts_kernel_tree_alias_and_custom_artifact_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            artifact_root = "out/c2saferust-artifacts"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--kernel-tree",
                    str(root),
                    "--artifact-root",
                    artifact_root,
                    "--module-path",
                    "drivers/net/nlmon.c",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            target = root / artifact_root / "nlmon" / "translation-plan.json"
            self.assertTrue(target.exists())
            self.assertIn(
                "out/c2saferust-artifacts/nlmon/translation-plan.json",
                manifest["generated_artifacts"],
            )

    def test_translation_plan_can_repoint_artifact_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            payload = intake.build_translation_plan(
                "drivers/net/nlmon.c",
                repo_root=root,
                artifact_root=root / "out" / "artifacts",
            )

            self.assertEqual(
                payload["inputs"]["abstraction_plan"],
                "out/artifacts/nlmon/abstraction-plan.json",
            )
            self.assertEqual(
                payload["inputs"]["unsafe_obligations"],
                "out/artifacts/nlmon/unsafe-obligations.json",
            )

    def test_vsockmon_profile_generates_static_closure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            abstraction_payload = intake.build_abstraction_plan("drivers/net/vsockmon.c", repo_root=root)
            translation_payload = intake.build_translation_plan("drivers/net/vsockmon.c", repo_root=root)

            self.assertEqual(abstraction_payload["module_id"], "vsockmon")
            statuses = {entry["area"]: entry["status"] for entry in abstraction_payload["abstraction_areas"]}
            self.assertEqual(statuses["vsock_tap"], "missing")
            blockers = translation_payload["readiness"]["blockers"]
            self.assertEqual(len(blockers), len(set(blockers)))
            self.assertFalse(translation_payload["readiness"]["ready_for_minimal_driver_codegen"])
            self.assertEqual(translation_payload["driver_rust_path"], "drivers/net/vsockmon_rust.rs")
            self.assertIn("kernel::net::vsock_tap::Tap", translation_payload["allowed_driver_surfaces"])

    def test_apply_oracle_feedback_writes_feedback_and_refreshes_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            feedback_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "oracle-feedback.json"
            run_record_path = root / "run-record.json"
            run_record_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "toolchain-run-record",
                        "module_id": "nlmon",
                        "scenario_id": "ip_link_lifecycle",
                        "qemu": {"log_path": str(root / "missing.log")},
                        "smoke_summary": {
                            "result": "FAIL",
                            "add_rc": 0,
                            "up_rc": 1,
                            "show_rc": 125,
                            "down_rc": 125,
                            "del_rc": 125,
                        },
                    }
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "apply-oracle-feedback",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/net/nlmon.c",
                    "--run-record",
                    str(run_record_path),
                    "--output",
                    str(feedback_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["artifact_type"], "oracle-feedback")
            self.assertIn("promote-xmit-from-oracle", payload["triggered_rules"])
            refreshed_translation = json.loads(
                (root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "translation-plan.json").read_text()
            )
            self.assertIn("oracle_feedback_notes", refreshed_translation)

    def test_apply_oracle_feedback_reads_runner_log_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            feedback_path = root / "Documentation" / "rust" / "c2saferust" / "nlmon" / "oracle-feedback.json"
            run_record_path = root / "run-record.json"
            log_path = root / "oracle.log"
            log_path.write_text("[smoke] sample log\n")
            run_record_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "toolchain-run-record",
                        "module_id": "nlmon",
                        "scenario_id": "ip_link_lifecycle",
                        "runner": {
                            "id": "qemu-scenario",
                            "log_path": str(log_path),
                        },
                        "smoke_summary": {
                            "result": "PASS",
                            "add_rc": 0,
                            "up_rc": 0,
                            "show_rc": 0,
                            "down_rc": 0,
                            "del_rc": 0,
                        },
                    }
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "apply-oracle-feedback",
                    "--repo-root",
                    str(root),
                    "--module-path",
                    "drivers/net/nlmon.c",
                    "--run-record",
                    str(run_record_path),
                    "--output",
                    str(feedback_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["qemu_log_path"], str(log_path))

    def test_build_safety_policy_relaxes_soundness_rule_via_oracle_feedback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            source_root = self._build_external_goldfish_source(root)
            feedback_path = (
                root
                / "Documentation"
                / "rust"
                / "c2saferust"
                / "goldfish_address_space"
                / "oracle-feedback.json"
            )
            feedback_path.parent.mkdir(parents=True, exist_ok=True)
            feedback_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "oracle-feedback",
                        "module_id": "goldfish_address_space",
                        "actions": [
                            {
                                "type": "relax_soundness_rule",
                                "targets": ["safety-policy"],
                                "rule_id": "page-owned-ping-buffer-api",
                                "remove_must_contain": ["pub fn copy_from_user_slice("],
                            }
                        ],
                    }
                )
            )

            payload = intake.build_safety_policy(
                profile_id="goldfish_address_space",
                repo_root=root,
                source_tree=source_root,
            )
            page_rule = next(
                rule
                for rule in payload["abstraction_policy"]["required_soundness_rules"]
                if rule["id"] == "page-owned-ping-buffer-api"
            )
            self.assertNotIn("pub fn copy_from_user_slice(", page_rule["must_contain"])

    def test_refresh_artifacts_supports_ax88796b_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "ax88796b",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(
                "Documentation/rust/c2saferust/ax88796b/translation-plan.json",
                payload["generated_artifacts"],
            )

            abstraction = json.loads(
                (
                    root
                    / "Documentation"
                    / "rust"
                    / "c2saferust"
                    / "ax88796b"
                    / "abstraction-plan.json"
                ).read_text()
            )
            translation = json.loads(
                (
                    root
                    / "Documentation"
                    / "rust"
                    / "c2saferust"
                    / "ax88796b"
                    / "translation-plan.json"
                ).read_text()
            )

            phy_field_map = abstraction["source_inventory"]["callback_tables"]["phy_driver"]["field_map"]
            self.assertEqual(phy_field_map["read_status"], "asix_ax88772a_read_status")
            self.assertEqual(phy_field_map["link_change_notify"], "asix_ax88772a_link_change_notify")
            callback_status = {entry["c_symbol"]: entry["status"] for entry in translation["callback_mapping"]}
            self.assertEqual(callback_status["asix_ax88772a_read_status"], "required")
            self.assertEqual(callback_status["genphy_suspend"], "required")
            self.assertEqual(callback_status["genphy_resume"], "required")
            self.assertEqual(callback_status["asix_soft_reset"], "required")
            self.assertEqual(callback_status["asix_ax88772a_link_change_notify"], "required")

    def test_refresh_artifacts_supports_ax88796b_blind_strict_profile_without_reference_driver(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "ax88796b_blind_strict",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(
                "Documentation/rust/c2saferust/ax88796b_blind_strict/translation-plan.json",
                payload["generated_artifacts"],
            )

            abstraction = json.loads(
                (
                    root
                    / "Documentation"
                    / "rust"
                    / "c2saferust"
                    / "ax88796b_blind_strict"
                    / "abstraction-plan.json"
                ).read_text()
            )
            translation = json.loads(
                (
                    root
                    / "Documentation"
                    / "rust"
                    / "c2saferust"
                    / "ax88796b_blind_strict"
                    / "translation-plan.json"
                ).read_text()
            )

            self.assertEqual(abstraction["module_id"], "ax88796b_blind_strict")
            self.assertTrue(translation["readiness"]["ready_for_minimal_driver_codegen"])
            rules_text = "\n".join(abstraction["phase_4_readiness"]["driver_generation_rules"])
            self.assertNotIn("drivers/net/phy/ax88796b_rust.rs", rules_text)

    def test_refresh_artifacts_supports_ax88796b_blind_strict_v2_without_helper_recipes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "ax88796b_blind_strict_v2",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(
                "Documentation/rust/c2saferust/ax88796b_blind_strict_v2/module-lifecycle.config",
                payload["generated_artifacts"],
            )

            artifact_root = root / "Documentation" / "rust" / "c2saferust" / "ax88796b_blind_strict_v2"
            translation = json.loads((artifact_root / "translation-plan.json").read_text())
            workflow_payload = intake.build_agent_workflow_plan(
                repo_root=root,
                profile_id="ax88796b_blind_strict_v2",
            )
            lifecycle_config = (artifact_root / "module-lifecycle.config").read_text()

            mapping = {entry["c_symbol"]: entry for entry in translation["callback_mapping"]}
            self.assertIn("behavior_contract", mapping["asix_ax88772a_read_status"])
            self.assertNotIn("body_plan", mapping["asix_ax88772a_read_status"])
            self.assertNotIn("rust_surface", mapping["asix_ax88772a_read_status"])
            self.assertEqual(mapping["asix_ax88772a_read_status"].get("c_evidence"), ["!phydev->link"])
            self.assertNotIn("c_evidence", mapping["genphy_suspend"])
            self.assertNotIn("c_evidence", mapping["genphy_resume"])
            self.assertNotIn("c_evidence", mapping["asix_soft_reset"])
            self.assertEqual(
                mapping["asix_ax88772a_link_change_notify"].get("c_evidence"),
                ["phydev->state == PHY_NOLINK"],
            )
            translation_text = json.dumps(translation, ensure_ascii=False)
            self.assertNotIn("dev.genphy_resume()", translation_text)
            self.assertNotIn("dev.genphy_suspend()", translation_text)
            self.assertNotIn("dev.start_aneg()", translation_text)
            self.assertNotIn("dev.init_hw()", translation_text)
            self.assertNotIn("genphy_update_link(phydev)", translation_text)
            self.assertNotIn("genphy_read_lpa(phydev)", translation_text)
            self.assertNotIn("phy_init_hw(phydev)", translation_text)
            self.assertNotIn("_phy_start_aneg(phydev)", translation_text)
            self.assertIn("CONFIG_MDIO_DEVICE=y", lifecycle_config)
            self.assertIn("CONFIG_AX88796B_PHY=m", lifecycle_config)
            self.assertIn("CONFIG_AX88796B_RUST_PHY=y", lifecycle_config)
            self.assertIn(
                "Documentation/rust/c2saferust/ax88796b_blind_strict_v2/module-lifecycle.config",
                workflow_payload["preflight"]["required_reads"],
            )


    def test_bootstrap_benchmark_supports_ax88796b_ground_truth_strict_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output_dir / "module-manifest.json").read_text())
            provenance_log = output_dir / "provenance-log.jsonl"

            self.assertTrue(provenance_log.exists())
            self.assertEqual(manifest["module_id"], "ax88796b_blind_strict")
            self.assertIn(
                str((root / "drivers" / "net" / "phy" / "ax88796b.c").resolve()),
                manifest["search_hygiene"]["search_allowlist"],
            )
            self.assertIn(
                str((root / "Documentation" / "rust" / "c2saferust" / "ax88796b_blind_strict").resolve()),
                manifest["search_hygiene"]["search_allowlist"],
            )
            self.assertIn(
                str((root / "drivers" / "net" / "phy" / "ax88796b_rust.rs").resolve()),
                manifest["search_hygiene"]["forbidden_reference_paths"],
            )
            self.assertIn(
                "/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-*",
                manifest["search_hygiene"]["forbidden_reference_paths"],
            )

    def test_bootstrap_benchmark_supports_ax88796b_ground_truth_strict_v2_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict-v2"

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "ax88796b_blind_strict_v2",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output_dir / "module-manifest.json").read_text())
            provenance_log = output_dir / "provenance-log.jsonl"
            provenance_log_text = output_dir / "provenance_log.txt"

            self.assertTrue(provenance_log.exists())
            self.assertTrue(provenance_log_text.exists())
            self.assertEqual(manifest["module_id"], "ax88796b_blind_strict_v2")
            self.assertIn(
                "module_lifecycle_config",
                manifest["planning_artifacts"],
            )
            self.assertEqual(
                manifest["blind_policy"]["physical_absence_required_paths"],
                ["drivers/net/phy/ax88796b_rust.rs"],
            )
            self.assertIn(
                str((root / "Documentation" / "rust" / "c2saferust" / "ax88796b_blind_strict_v2").resolve()),
                manifest["search_hygiene"]["search_allowlist"],
            )
            manifest_text = json.dumps(manifest, ensure_ascii=False)
            self.assertNotIn("dev.genphy_resume()", manifest_text)
            self.assertNotIn("dev.genphy_suspend()", manifest_text)

    def test_build_difference_metrics_requires_freeze_for_strict_v2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict-v2"
            candidate = root / "candidate.rs"
            reference = root / "reference.rs"
            output = benchmark_dir / "difference-metrics.json"

            candidate.write_text("fn read_status() {}\n")
            reference.write_text("fn read_status() {}\n")

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-artifacts",
                    "--repo-root",
                    str(root),
                    "--profile-id",
                    "ax88796b_blind_strict_v2",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            blocked = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-difference-metrics",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                    "--candidate",
                    str(candidate),
                    "--reference",
                    str(reference),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("freeze", blocked.stderr)

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "record-benchmark-provenance",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                    "--kind",
                    "freeze",
                    "--note",
                    "candidate and artifacts frozen before compare",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            allowed = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-difference-metrics",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                    "--candidate",
                    str(candidate),
                    "--reference",
                    str(reference),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue(output.exists())

    def test_record_benchmark_provenance_marks_summary_contaminated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict"

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output-dir",
                    str(benchmark_dir),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "record-benchmark-provenance",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--kind",
                    "contamination",
                    "--contamination-status",
                    "contaminated",
                    "--note",
                    "reference driver body was exposed before freeze",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            refresh = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-benchmark-summary",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output",
                    str(benchmark_dir / "validator-ready-summary.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(refresh.returncode, 0, refresh.stderr)
            payload = json.loads(refresh.stdout)
            self.assertEqual(payload["contamination_status"], "contaminated")
            self.assertGreaterEqual(payload["provenance"]["contamination_event_count"], 1)

    def test_init_blind_worktree_pair_removes_reference_driver_from_blind_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            repo = temp_root / "repo"
            blind_tree = temp_root / "blind"
            reference_tree = temp_root / "reference"
            self._build_sample_root(repo, include_ax88796b_reference=True)

            subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, text=True, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "initial",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "init-blind-worktree-pair",
                    "--repo-root",
                    str(repo),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict-v2",
                    "--baseline-rev",
                    "HEAD",
                    "--blind-tree",
                    str(blind_tree),
                    "--reference-tree",
                    str(reference_tree),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue((reference_tree / "drivers" / "net" / "phy" / "ax88796b_rust.rs").exists())
            self.assertFalse((blind_tree / "drivers" / "net" / "phy" / "ax88796b_rust.rs").exists())
            seal = json.loads((blind_tree / ".c2saferust-blind-seal.json").read_text())
            self.assertEqual(payload["benchmark_id"], "ax88796b-ground-truth-strict-v2")
            self.assertIn("drivers/net/phy/ax88796b_rust.rs", payload["removed_paths"])
            self.assertEqual(seal["baseline_commit"], payload["baseline_commit"])

    def test_bootstrap_benchmark_writes_manifest_contract_and_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            output_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "nlmon-ground-truth"

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "nlmon-ground-truth",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["artifact_type"], "benchmark-bootstrap-manifest")

            manifest_path = output_dir / "module-manifest.json"
            contract_path = output_dir / "planner-contract.json"
            summary_path = output_dir / "validator-ready-summary.json"
            provenance_text_path = output_dir / "provenance_log.txt"
            self.assertTrue(manifest_path.exists())
            self.assertTrue(contract_path.exists())
            self.assertTrue(summary_path.exists())
            self.assertTrue(provenance_text_path.exists())

            manifest = json.loads(manifest_path.read_text())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(manifest["benchmark_id"], "nlmon-ground-truth")
            self.assertEqual(manifest["module_id"], "nlmon")
            self.assertEqual(summary["runtime_state"], "unvalidated")
            self.assertEqual(summary["target_runtime_state"], "differential_validated")

    def test_benchmark_summary_and_report_distinguish_runner_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict"

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output-dir",
                    str(benchmark_dir),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            manifest = json.loads((benchmark_dir / "module-manifest.json").read_text())
            toolchain_record = root / manifest["planning_artifacts"]["toolchain_run_record"]
            toolchain_record.parent.mkdir(parents=True, exist_ok=True)
            intake.write_json(
                toolchain_record,
                {
                    "artifact_type": "toolchain-run-record",
                    "module_id": "ax88796b_blind_strict",
                    "runner_id": "kunit-synthetic-mdio",
                    "oracle_runner": "kunit-synthetic-mdio",
                    "pass": False,
                    "ready_for_oracle": False,
                    "blocked": True,
                    "evidence_tier": "env_blocked",
                    "blocker_kind": "runner-unavailable",
                    "blockers": ["Unknown oracle runner `kunit-synthetic-mdio`."],
                    "runner": {
                        "id": "kunit-synthetic-mdio",
                        "status": "runner-unavailable",
                    },
                    "smoke_summary": {"result": "BLOCKED"},
                    "steps": [],
                },
            )

            summary = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-benchmark-summary",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output",
                    str(benchmark_dir / "validator-ready-summary.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(summary.returncode, 0, summary.stderr)
            summary_payload = json.loads(summary.stdout)
            self.assertEqual(summary_payload["runtime_assessment"]["status"], "runner_unavailable")

            report = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-benchmark-experiment-report",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output",
                    str(benchmark_dir / "experiment-report.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(report.returncode, 0, report.stderr)
            report_payload = json.loads(report.stdout)
            self.assertEqual(report_payload["runtime_assessment"]["status"], "runner_unavailable")

    def test_benchmark_summary_and_report_distinguish_candidate_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root, include_ax88796b_reference=False)
            benchmark_dir = root / "Documentation" / "rust" / "c2saferust" / "benchmarks" / "ax88796b-ground-truth-strict"

            subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "bootstrap-benchmark",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output-dir",
                    str(benchmark_dir),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            manifest = json.loads((benchmark_dir / "module-manifest.json").read_text())
            lifecycle_record = Path(manifest["benchmark_artifacts"]["lifecycle_oracle_run_record"])
            lifecycle_record.parent.mkdir(parents=True, exist_ok=True)
            intake.write_json(
                lifecycle_record,
                {
                    "artifact_type": "lifecycle-oracle-run-record",
                    "module_id": "ax88796b_blind_strict",
                    "runner_id": "qemu-module-lifecycle",
                    "oracle_runner": "qemu-module-lifecycle",
                    "pass": False,
                    "ready_for_oracle": True,
                    "blocked": False,
                    "evidence_tier": "env_ready",
                    "runner": {
                        "id": "qemu-module-lifecycle",
                        "status": "fail",
                    },
                    "smoke_summary": {"result": "FAIL"},
                    "steps": [],
                },
            )

            summary = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "refresh-benchmark-summary",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output",
                    str(benchmark_dir / "validator-ready-summary.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(summary.returncode, 0, summary.stderr)
            summary_payload = json.loads(summary.stdout)
            self.assertEqual(summary_payload["runtime_assessment"]["status"], "candidate_failed")

            report = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-benchmark-experiment-report",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "ax88796b-ground-truth-strict",
                    "--output",
                    str(benchmark_dir / "experiment-report.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(report.returncode, 0, report.stderr)
            report_payload = json.loads(report.stdout)
            self.assertEqual(report_payload["runtime_assessment"]["status"], "candidate_failed")

    def test_build_difference_metrics_reports_api_and_callback_fit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate = root / "candidate.rs"
            reference = root / "reference.rs"
            output = root / "difference-metrics.json"

            candidate.write_text(
                "use kernel::net::{netdevice, netlink_tap, rtnl, stats};\n"
                "struct NlmonDriver(netlink_tap::Tap);\n"
                "impl netdevice::Operations for NlmonDriver {\n"
                "    fn open() {}\n"
                "    fn setup() {}\n"
                "    fn stop() {}\n"
                "    fn start_xmit() { stats::dev_lstats_add(); }\n"
                "}\n"
                "impl rtnl::Driver for NlmonDriver {}\n"
                "fn register() { let _ = rtnl::Registration::<NlmonDriver>::new(); }\n"
            )
            reference.write_text(
                "use kernel::net::{netdevice, netlink_tap, rtnl, stats};\n"
                "struct NlmonDriver(netlink_tap::Tap);\n"
                "impl netdevice::Operations for NlmonDriver {\n"
                "    fn setup() {}\n"
                "    fn validate() {}\n"
                "    fn open() {}\n"
                "    fn stop() {}\n"
                "    fn start_xmit() { stats::dev_lstats_add(); }\n"
                "}\n"
                "impl rtnl::Driver for NlmonDriver {}\n"
                "fn register() { let _ = rtnl::Registration::<NlmonDriver>::new(); }\n"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "build-difference-metrics",
                    "--repo-root",
                    str(root),
                    "--benchmark-id",
                    "nlmon-ground-truth",
                    "--candidate",
                    str(candidate),
                    "--reference",
                    str(reference),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(output.exists())
            self.assertEqual(payload["api_fit"]["api_hit_rate"], 1.0)
            self.assertEqual(payload["callback_fit"]["callback_coverage_rate"], 0.8)

    def test_collect_benchmark_portfolio_writes_json_and_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            benchmark_root = root / "Documentation" / "rust" / "c2saferust" / "benchmarks"
            json_output = root / "portfolio.json"
            csv_output = root / "portfolio.csv"

            def write_sample(
                benchmark_id: str,
                module_id: str,
                runtime_state: str,
                api_hit_rate: float,
                callback_coverage_rate: float,
            ) -> None:
                sample_dir = benchmark_root / benchmark_id
                sample_dir.mkdir(parents=True, exist_ok=True)
                manifest = {
                    "benchmark_id": benchmark_id,
                    "module_id": module_id,
                    "benchmark_track": "formal-ground-truth",
                    "sample_tier": "small",
                    "scope": "full_driver",
                    "quota_class": "phase1-formal",
                    "conversion_stage": "blind",
                    "style_alignment": "benchmark-calibrated",
                    "runtime_state": runtime_state,
                    "target_runtime_state": runtime_state,
                    "claim_ceiling": "sample claim",
                }
                summary = {
                    "conversion_stage": "blind",
                    "style_alignment": "benchmark-calibrated",
                    "runtime_state": runtime_state,
                    "checks": {
                        "safety_pass": True,
                        "ready_for_smoke": True,
                        "compile_pass": True,
                        "toolchain_run_pass": runtime_state in {"behavior_validated", "differential_validated"},
                        "lifecycle_run_pass": runtime_state in {"lifecycle_validated", "differential_validated"},
                        "differential_run_pass": runtime_state == "differential_validated",
                    },
                    "claims": {
                        "current_claim_ceiling": "sample claim",
                    },
                }
                metrics = {
                    "candidate": {
                        "unsafe_sites": 0,
                        "bindings_direct_uses": 0,
                    },
                    "reference": {
                        "unsafe_sites": 1,
                        "bindings_direct_uses": 2,
                    },
                    "api_fit": {
                        "api_hit_rate": api_hit_rate,
                    },
                    "callback_fit": {
                        "callback_coverage_rate": callback_coverage_rate,
                    },
                    "size": {
                        "size_ratio": 0.9,
                    },
                }
                intake.write_json(sample_dir / "module-manifest.json", manifest)
                intake.write_json(sample_dir / "validator-ready-summary.json", summary)
                intake.write_json(sample_dir / "difference-metrics.json", metrics)

            write_sample("nlmon-ground-truth", "nlmon", "differential_validated", 1.0, 1.0)
            write_sample("ax88796b-ground-truth", "ax88796b", "lifecycle_validated", 0.75, 1.0)

            result = subprocess.run(
                [
                    sys.executable,
                    str(self.SCRIPT_PATH),
                    "collect-benchmark-portfolio",
                    "--root",
                    str(benchmark_root),
                    "--output",
                    str(json_output),
                    "--csv-output",
                    str(csv_output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["summary"]["total_benchmarks"], 2)
            self.assertEqual(payload["summary"]["runtime_state_counts"]["differential_validated"], 1)
            self.assertEqual(payload["summary"]["runtime_state_counts"]["lifecycle_validated"], 1)
            self.assertTrue(json_output.exists())
            self.assertTrue(csv_output.exists())
            self.assertEqual(len(csv_output.read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
