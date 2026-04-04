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
import tool_cli  # noqa: E402


class C2SafeRustToolTests(unittest.TestCase):
    SCRIPT_PATH = TOOL_DIR / "tool_cli.py"

    def _build_sample_root(self, root: Path) -> None:
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
        (root / "drivers" / "net" / "phy").mkdir(parents=True, exist_ok=True)
        (root / "drivers" / "net" / "phy" / "Makefile").write_text(
            "ifdef CONFIG_LSI_ET1011C_RUST_PHY\n"
            "  obj-$(CONFIG_LSI_ET1011C_PHY)\t+= et1011c_rust.o\n"
            "else\n"
            "  obj-$(CONFIG_LSI_ET1011C_PHY)\t+= et1011c.o\n"
            "endif\n"
            "ifdef CONFIG_QSEMI_RUST_PHY\n"
            "  obj-$(CONFIG_QSEMI_PHY)\t+= qsemi_rust.o\n"
            "else\n"
            "  obj-$(CONFIG_QSEMI_PHY)\t+= qsemi.o\n"
            "endif\n"
            "ifdef CONFIG_AX88796B_RUST_PHY\n"
            "  obj-$(CONFIG_AX88796B_PHY)\t+= ax88796b_rust.o\n"
            "else\n"
            "  obj-$(CONFIG_AX88796B_PHY)\t+= ax88796b.o\n"
            "endif\n"
        )
        (root / "drivers" / "net" / "phy" / "Kconfig").write_text(
            "config RUST_PHYLIB_ABSTRACTIONS\n\tbool\n"
            "config LSI_ET1011C_PHY\n\ttristate \"LSI ET1011C PHY\"\n"
            "config LSI_ET1011C_RUST_PHY\n\tbool \"Rust reference driver for LSI ET1011C PHY\"\n\tdepends on RUST_PHYLIB_ABSTRACTIONS && LSI_ET1011C_PHY\n"
            "config QSEMI_PHY\n\ttristate \"Quality Semiconductor PHYs\"\n"
            "config QSEMI_RUST_PHY\n\tbool \"Rust reference driver for Quality Semiconductor PHYs\"\n\tdepends on RUST_PHYLIB_ABSTRACTIONS && QSEMI_PHY\n"
            "config AX88796B_PHY\n\ttristate \"ASIX PHY\"\n"
        )
        (root / "drivers" / "net" / "phy" / "et1011c.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/mii.h>
#include <linux/phy.h>

static int et1011c_config_aneg(struct phy_device *phydev)
{
\tint bmcr = phy_read(phydev, MII_BMCR);
\tphy_write(phydev, MII_BMCR, bmcr);
\treturn genphy_config_aneg(phydev);
}

static int et1011c_read_status(struct phy_device *phydev)
{
\tint value = phy_read(phydev, MII_BMSR);
\tif (value >= 0)
\t\tphy_write(phydev, MII_BMSR, value);
\treturn genphy_read_status(phydev);
}

static struct phy_driver et1011c_driver = {
\t.config_aneg = et1011c_config_aneg,
\t.read_status = et1011c_read_status,
};
"""
        )
        (root / "drivers" / "net" / "phy" / "qsemi.c").write_text(
            """// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/phy.h>

static int qs6612_config_init(struct phy_device *phydev)
{
\treturn phy_write(phydev, 0x1f, 0x0001);
}

static int qs6612_config_intr(struct phy_device *phydev)
{
\tif (phydev->interrupts == PHY_INTERRUPT_ENABLED)
\t\treturn phy_write(phydev, 0x12, 0x0001);
\treturn phy_write(phydev, 0x12, 0x0000);
}

static int qs6612_handle_interrupt(struct phy_device *phydev)
{
\tint irq_status = phy_read(phydev, 0x13);
\tif (irq_status < 0) {
\t\tphy_error(phydev);
\t\treturn irq_status;
\t}
\tphy_trigger_machine(phydev);
\treturn 0;
}

static struct phy_driver qsemi_driver = {
\t.config_init = qs6612_config_init,
\t.config_intr = qs6612_config_intr,
\t.handle_interrupt = qs6612_handle_interrupt,
};
"""
        )
        (root / "drivers" / "net" / "phy" / "ax88796b_rust.rs").write_text("// reference\n")
        (root / "drivers" / "net" / "phy" / "et1011c_rust.rs").write_text(
            "#![forbid(unsafe_code)]\nmodule_phy_driver! {}\n"
        )
        (root / "drivers" / "net" / "phy" / "qsemi_rust.rs").write_text(
            "#![forbid(unsafe_code)]\nmodule_phy_driver! {}\n"
        )
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
        (root / "rust" / "kernel" / "net" / "phy.rs").write_text(
            "use crate::{bindings, prelude::*};\n"
            "pub struct Device;\n"
            "impl Device {\n"
            "\tpub fn genphy_config_aneg(&mut self) -> Result {\n"
            "\t\tlet _ = bindings::__genphy_config_aneg;\n"
            "\t\tOk(())\n"
            "\t}\n"
            "}\n"
            "pub trait Driver {\n"
            "\tfn config_aneg(dev: &mut Device) -> Result;\n"
            "\tfn read_status(dev: &mut Device) -> Result;\n"
            "\tfn config_init(dev: &mut Device) -> Result { let _ = dev; Ok(()) }\n"
            "\tfn config_intr(dev: &mut Device) -> Result { let _ = dev; Ok(()) }\n"
            "\tfn handle_interrupt(dev: &mut Device) -> Result { let _ = dev; Ok(()) }\n"
            "}\n"
        )
        (root / "rust" / "kernel" / "net" / "phy").mkdir(parents=True, exist_ok=True)
        (root / "rust" / "kernel" / "net" / "phy" / "reg.rs").write_text("// reg\n")

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
            self.assertEqual(gate_ids, ["verify-safety", "compile-driver-object"])

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

    def test_oracle_runner_registry_exposes_module_lifecycle_runner(self):
        self.assertIn("qemu-module-lifecycle", oracle_runners.available_runner_ids())

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
            self.assertEqual(gate_status["compile-driver-object"], "blocked")

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
            self.assertEqual(gate_status["compile-driver-object"], "skipped")

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
            output="/tmp/out.json",
            qemu_log_output="/tmp/log.txt",
            build_dir="/tmp/build",
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
                "helper-audit.json",
                "kbuild-patch-plan.json",
                "bindings-patch-plan.json",
                "helpers-patch-plan.json",
                "abstraction-plan.json",
                "unsafe-obligations.json",
                "translation-plan.json",
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

    def test_net_phy_kbuild_profile_override_is_reflected_in_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            payload = intake.build_kbuild_plan("drivers/net/phy/et1011c.c", repo_root=root)
            self.assertEqual(payload["suggested_rust_config_symbol"], "LSI_ET1011C_RUST_PHY")
            self.assertIn(
                "RUST_PHYLIB_ABSTRACTIONS && LSI_ET1011C_PHY",
                "\n".join(payload["suggested_kconfig_snippet"]),
            )

    def test_et1011c_safety_policy_uses_only_core_phy_trampoline_requirements(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            policy = intake.build_safety_policy("drivers/net/phy/et1011c.c", repo_root=root)
            rules = {
                entry["id"]: entry
                for entry in policy["abstraction_policy"]["required_soundness_rules"]
            }
            self.assertIn("phy-callback-trampolines", rules)
            self.assertNotIn("phy-interrupt-callback-trampolines", rules)
            must_contain = rules["phy-callback-trampolines"]["must_contain"]
            self.assertNotIn('unsafe extern "C" fn config_init_callback(', must_contain)
            self.assertNotIn('unsafe extern "C" fn config_intr_callback(', must_contain)
            self.assertNotIn('unsafe extern "C" fn handle_interrupt_callback(', must_contain)

    def test_qsemi_safety_policy_adds_irq_trampoline_requirements(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            policy = intake.build_safety_policy("drivers/net/phy/qsemi.c", repo_root=root)
            rules = {
                entry["id"]: entry
                for entry in policy["abstraction_policy"]["required_soundness_rules"]
            }
            self.assertIn("phy-callback-trampolines", rules)
            self.assertIn("phy-interrupt-callback-trampolines", rules)
            must_contain = rules["phy-interrupt-callback-trampolines"]["must_contain"]
            self.assertIn('unsafe extern "C" fn config_init_callback(', must_contain)
            self.assertIn('unsafe extern "C" fn config_intr_callback(', must_contain)
            self.assertIn('unsafe extern "C" fn handle_interrupt_callback(', must_contain)

    def test_generic_scenario_inputs_render_module_lifecycle_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            translation = intake.build_translation_plan("drivers/net/phy/et1011c.c", repo_root=root)
            module_profile = smoke.profiles.load_module_profile("drivers/net/phy/et1011c.c")
            rendered = smoke._render_scenario_inputs("drivers/net/phy/et1011c.c", module_profile, translation)
            self.assertEqual(rendered["module_name"], "et1011c_rust")
            self.assertEqual(rendered["module_file_name"], "et1011c_rust.ko")
            self.assertEqual(rendered["module_file_relpath"], "drivers/net/phy/et1011c_rust.ko")

    def test_net_phy_translation_plan_is_ready_after_et1011c_landing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            payload = intake.build_translation_plan("drivers/net/phy/et1011c.c", repo_root=root)
            self.assertTrue(payload["readiness"]["ready_for_minimal_driver_codegen"])
            self.assertEqual(
                [entry["c_symbol"] for entry in payload["callback_mapping"]],
                ["et1011c_config_aneg", "et1011c_read_status"],
            )

    def test_qsemi_abstraction_plan_marks_irq_surfaces_implemented(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            payload = intake.build_abstraction_plan("drivers/net/phy/qsemi.c", repo_root=root)
            areas = {
                entry["area"]: entry["status"]
                for entry in payload["abstraction_areas"]
            }
            self.assertEqual(areas["phy_config_init"], "implemented")
            self.assertEqual(areas["phy_irq_callbacks"], "implemented")
            self.assertTrue(payload["phase_4_readiness"]["ready_for_minimal_driver_codegen"])

    def test_qsemi_scenario_inputs_render_module_lifecycle_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            translation = intake.build_translation_plan("drivers/net/phy/qsemi.c", repo_root=root)
            module_profile = smoke.profiles.load_module_profile("drivers/net/phy/qsemi.c")
            rendered = smoke._render_scenario_inputs("drivers/net/phy/qsemi.c", module_profile, translation)
            self.assertEqual(rendered["module_name"], "qsemi_rust")
            self.assertEqual(rendered["module_file_name"], "qsemi_rust.ko")
            self.assertEqual(rendered["module_file_relpath"], "drivers/net/phy/qsemi_rust.ko")

    def test_qsemi_translation_plan_is_ready_after_stage2_landing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_sample_root(root)
            payload = intake.build_translation_plan("drivers/net/phy/qsemi.c", repo_root=root)
            self.assertTrue(payload["readiness"]["ready_for_minimal_driver_codegen"])
            self.assertEqual(
                [entry["c_symbol"] for entry in payload["callback_mapping"]],
                ["qs6612_config_init", "qs6612_config_intr", "qs6612_handle_interrupt"],
            )


if __name__ == "__main__":
    unittest.main()
