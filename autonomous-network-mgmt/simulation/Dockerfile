FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    mininet \
    openvswitch-switch \
    net-tools iproute2 iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY topology.py mock_snmp_agent.py metric_generator.py ./

# OVS 데몬 시작 후 Flask 서버 실행
CMD ["bash", "-c", "service openvswitch-switch start && python3 mock_snmp_agent.py"]
