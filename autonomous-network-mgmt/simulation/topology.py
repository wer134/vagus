"""Mininet 토폴로지 — 라우터 4개, 링크 6개 (풀메시에 가까운 구조).

실행: sudo python3 topology.py
"""
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI


# 링크 파라미터 기본값
DEFAULT_BW = 1000    # Mbps
DEFAULT_DELAY = "5ms"
DEFAULT_LOSS = 0     # %


def build_topology():
    """4-라우터 토폴로지 생성 후 Mininet 인스턴스 반환."""
    net = Mininet(switch=OVSKernelSwitch, link=TCLink, controller=None)

    # 라우터 역할 스위치 (실장비 없이 OVS로 대체)
    r1 = net.addSwitch("r1", dpid="0000000000000001")
    r2 = net.addSwitch("r2", dpid="0000000000000002")
    r3 = net.addSwitch("r3", dpid="0000000000000003")
    r4 = net.addSwitch("r4", dpid="0000000000000004")

    # 호스트 (각 라우터에 1개씩 연결)
    h1 = net.addHost("h1", ip="10.0.1.1/24")
    h2 = net.addHost("h2", ip="10.0.2.1/24")
    h3 = net.addHost("h3", ip="10.0.3.1/24")
    h4 = net.addHost("h4", ip="10.0.4.1/24")

    # 링크 정의: (src, dst, ospf_cost)
    links = [
        (r1, r2, 10),
        (r1, r3, 10),
        (r2, r3, 10),
        (r2, r4, 10),
        (r3, r4, 10),
        (r1, r4, 10),
    ]

    for src, dst, _ in links:
        net.addLink(
            src, dst,
            bw=DEFAULT_BW,
            delay=DEFAULT_DELAY,
            loss=DEFAULT_LOSS,
            use_htb=True,
        )

    # 호스트 ↔ 라우터 연결
    net.addLink(h1, r1)
    net.addLink(h2, r2)
    net.addLink(h3, r3)
    net.addLink(h4, r4)

    return net


def set_link_param(net: Mininet, src_name: str, dst_name: str, **kwargs):
    """런타임 링크 파라미터 변경 (orchestrator에서 호출).

    kwargs 예: bw=500, delay='20ms', loss=0.01
    """
    src = net.get(src_name)
    dst = net.get(dst_name)
    for intf in src.intfList():
        link = intf.link
        if link and (link.intf1.node == dst or link.intf2.node == dst):
            link.intf1.config(**kwargs)
            link.intf2.config(**kwargs)
            return True
    return False


if __name__ == "__main__":
    setLogLevel("info")
    net = build_topology()
    net.start()
    print("Topology started. Nodes:", [n.name for n in net.hosts + net.switches])
    CLI(net)
    net.stop()
