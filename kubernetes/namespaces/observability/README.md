# Observability Namespace

This namespace contains comprehensive monitoring, logging, and alerting stack.

## Components

### Prometheus Stack (kube-prometheus-stack)
- **Prometheus Server**: Metrics collection and storage
- **Grafana**: Dashboards and visualization
- **AlertManager**: Alert routing and management
- **Node Exporters**: System metrics from all nodes (DaemonSet)
- **Kube State Metrics**: Kubernetes object metrics

### Loki Stack
- **Loki Distributed Components**:
  - Ingester (StatefulSet)
  - Querier (StatefulSet)
  - Distributor
  - Compactor
  - Gateway
  - Query Frontend
- **Storage**: 8Gi PVCs for ingester and querier
- **Caching**: Memcached for chunks and frontend

### Grafana Alloy (formerly Grafana Agent)
- **Logs Collection**: DaemonSet on all nodes
- **Metrics Collection**: Singleton deployment
- **Operator**: Managing Alloy configurations

## Services

### Prometheus Services
- `kube-prometheus-stack-prometheus` - Prometheus server (9090)
- `kube-prometheus-stack-grafana` - Grafana UI (80)
- `kube-prometheus-stack-alertmanager` - AlertManager (9093)
- `kube-prometheus-stack-operator` - Prometheus Operator (443)

### Loki Services
- `loki-grafana-loki-gateway` - Loki API gateway (80)
- `loki-grafana-loki-*` - Various Loki components (3100/9095)

### Alloy Services
- `k8s-monitoring-alloy-logs` - Log collection endpoint (12345)
- `k8s-monitoring-alloy-singleton` - Metrics collection (12345)

## Installation

```bash
# Install monitoring storage (Loki)
ansible-playbook ansible/02-install/04-install_monitoring_storages.yaml

# Install monitoring stack
ansible-playbook ansible/02-install/05-install_monitoring.yaml
```

## Configuration Files

- `grafana-values.yaml` - Grafana Helm values
- `loki-values.yaml` - Loki Helm values
- `k8s-monitoring-values.yml` - Alloy monitoring configuration

## Access

- Grafana Dashboard: http://grafana.observability.svc.cluster.local
- Prometheus: http://prometheus.observability.svc.cluster.local:9090
- Loki: http://loki-gateway.observability.svc.cluster.local

## Storage

- Total PVC usage: 32Gi across all components
- Retention policies configured in values files