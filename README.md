# vpp-pod

在 Kubernetes Pod 中运行 VPP。启动时根据 Pod 实际 cpuset 和 SR-IOV Device
Plugin 分配结果生成配置，避免硬编码 CPU 与 PCI 地址。

## 构建

仓库已内置 VPP v26.06 源码及其第三方源码归档，默认构建不访问 GitHub：

```bash
sudo docker build --network host -t vpp:26.06 .
```

`vpp/build/external/downloads/` 中包含 libdaq、intel-ipsec-mb、DPDK、rdma-core、
quicly、xdp-tools、libcbor 以及 DPDK 配置所需的 Python 包。VPP 构建系统会校验并
直接使用这些文件，不再现场从 GitHub 下载。Ubuntu 软件包仍由 `apt` 安装，因此目标
环境需要可用的 Ubuntu 软件源或对应的本地镜像源。

如需临时改回在线获取 VPP 主仓库，可显式指定：

```bash
sudo docker build --network host \
  --build-arg VPP_SOURCE=online \
  --build-arg VPP_REF=v26.06 \
  -t vpp:26.06 .
```

更新内置源码时，应同步替换 `vpp/` 并在联网环境重新准备与该版本匹配的
`vpp/build/external/downloads/`；不同 VPP 版本的依赖版本和校验值可能不同。

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

`USP_INTER_IP` 支持单地址 `10.2.0.222`，也支持范围
`10.2.0.222-10.2.0.225`；前缀长度通过 `USP_INTER_MASK` 单独配置，例如 `20`。
范围内所有地址都会使用该前缀长度配置到 `dpdk0`。

`VPP_DEFAULT_GATEWAY` 可配置为接口子网内的 IPv4 网关；为空或不提供时，
入口程序不会生成 IPv4 默认路由。示例 YAML 中默认使用 `10.2.7.254`。

入口程序只在可见 CPU 数量等于 `VPP_CPU_LIMIT` 后启动。一个 CPU 只生成
`main-core`；两个或更多 CPU 使用排序后的第一个作为 main，其余全部作为 worker。
入口程序立即检查 cpuset，数量不匹配时每 100 毫秒重试且没有超时，以适应当前环境
CPU Manager 延迟分配的行为。

可通过 ConfigMap 覆盖三个模板，但动态模板必须分别保留以下占位符且只能出现一次：

- `startup.conf.template`: `{{CPU_CONFIG}}`、`{{PCI_ADDRESS}}`
- `cli-commands.conf.template`: `{{INTERFACE_ADDRESS_COMMANDS}}`、`{{DEFAULT_ROUTE_COMMAND}}`
- `vcl.conf.template`: 无动态占位符

## 本地测试

```bash
python3 -m unittest discover -s tests -v
```
