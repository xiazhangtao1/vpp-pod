# vpp-pod

在 Kubernetes Pod 中运行 VPP。启动时根据 Pod 实际 cpuset 和 SR-IOV Device
Plugin 分配结果生成配置，避免硬编码 CPU 与 PCI 地址。

## 构建

```bash
sudo docker build --network host -t vpp:26.06 .
```

## 部署

1. 编辑 `k8s/vpp-pod.yaml` 中 ConfigMap 的地址和默认网关。
2. 确认节点提供 `intel.com/external_network`、至少 4 GiB 的 1 GiB hugepages，并且镜像可用。
3. CPU request 与 limit 必须相等且为正整数。

```bash
kubectl apply -f k8s/vpp-pod.yaml
kubectl logs -f pod/vpp
kubectl exec vpp -- vppctl -s /run/vpp/cli.sock show threads
kubectl exec vpp -- vppctl -s /run/vpp/cli.sock show interface address
kubectl exec vpp -- vppctl -s /run/vpp/cli.sock show ip fib
```

`VPP_INTERFACE_ADDRESSES` 支持单地址 `10.2.0.222/20`，也支持范围
`10.2.0.222-10.2.0.225/20`。范围内所有地址都会配置到 `dpdk0`。

入口程序只在可见 CPU 数量等于 `VPP_CPU_LIMIT` 后启动。一个 CPU 只生成
`main-core`；两个或更多 CPU 使用排序后的第一个作为 main，其余全部作为 worker。
等待没有超时，以适应当前环境 CPU Manager 延迟分配的行为。

可通过 ConfigMap 覆盖三个模板，但动态模板必须分别保留以下占位符且只能出现一次：

- `startup.conf.template`: `{{CPU_CONFIG}}`、`{{PCI_ADDRESS}}`
- `cli-commands.conf.template`: `{{INTERFACE_ADDRESS_COMMANDS}}`、`{{DEFAULT_GATEWAY}}`
- `vcl.conf.template`: 无动态占位符

## 本地测试

```bash
python3 -m unittest discover -s tests -v
```
