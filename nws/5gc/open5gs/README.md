# Open5GS 5G Core Network - Cross-Platform Docker Solution

A complete, production-ready 5G Core Network implementation using Open5GS, designed to work seamlessly on both **ARM64** and **x86_64** architectures. This solution provides a containerized 5G Core with automatic architecture detection and cross-platform compatibility.

## 🚀 Features

- **Cross-Platform Support**: Automatically detects and works on ARM64 and x86_64 architectures
- **Complete 5G Core**: All 5G Core Network Functions (NFs) included
- **Production Ready**: Optimized configuration with proper networking and security
- **Easy Deployment**: Single command deployment with Docker Compose
- **Web Management UI**: Built-in web interface for subscriber management
- **Automatic Setup**: Self-configuring with proper IP routing and NAT

## 📋 Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- Linux host with kernel 4.15+ (for TUN/TAP support)
- At least 4GB RAM and 2 CPU cores
- Network access for downloading dependencies

## 🏗️ Architecture

This solution includes all essential 5G Core Network Functions:

| Network Function | Component | Port | Description |
|------------------|-----------|------|-------------|
| **NRF** | Network Repository Function | 7777 | Service discovery and registration |
| **AMF** | Access and Mobility Management | 7777 | UE registration and mobility |
| **SMF** | Session Management Function | 7777 | PDU session management |
| **UPF** | User Plane Function | 8805, 2123, 2152 | Data forwarding |
| **AUSF** | Authentication Server Function | 7777 | UE authentication |
| **UDM** | Unified Data Management | 7777 | User data management |
| **UDR** | Unified Data Repository | 7777 | User data storage |
| **PCF** | Policy Control Function | 7777 | Policy decisions |
| **NSSF** | Network Slice Selection | 7777 | Slice selection |
| **BSF** | Binding Support Function | 7777 | Binding information |
| **SCP** | Service Communication Proxy | 7777 | Service mesh communication |
| **iperf3** | Network Performance Server | 5201, 5202, 5000-5003 | Network testing and benchmarking |

## 🚀 Quick Start

### 1. Clone and Navigate
```bash
git clone <repository-url>
cd core
```

### 2. Start the 5G Core
```bash
docker compose up -d
```

### 3. Verify Deployment
```bash
# Check container status
docker compose ps

# View logs
docker compose logs 5gc

# Check all network functions are running
docker exec open5gs_5gc ps aux | grep -E "(nrf|amf|smf|upf|ausf|udm|udr|pcf|nssf|bsf|scp)"
```

### 4. Access Web UI
Open your browser and navigate to: **http://localhost:9999**

### 5. Test Network Performance
The container includes multiple iperf3 servers for internal network testing:

```bash
# Test internal network performance (from within container)
docker exec open5gs_5gc iperf3 -c localhost -p 5201
docker exec open5gs_5gc iperf3 -c localhost -p 5202
docker exec open5gs_5gc iperf3 -c localhost -p 5000
docker exec open5gs_5gc iperf3 -c localhost -p 5001
docker exec open5gs_5gc iperf3 -c localhost -p 5002
docker exec open5gs_5gc iperf3 -c localhost -p 5003
```

## 🔧 Configuration

### Environment Variables

Key configuration parameters in `open5gs/open5gs.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPEN5GS_IP` | 10.53.1.2 | Core network IP address |
| `UE_IP_BASE` | 10.45.0 | UE IP address range |
| `N6_IP` | 10.45.0.1 | N6 interface IP |
| `DEBUG` | false | Enable debug mode |
| `SUBSCRIBER_DB` | subscriber_db.csv | Subscriber database file |

### Network Configuration

The core is deployed on a dedicated Docker bridge network named `nws-n2n3` (subnet: `10.53.1.0/24`).

*   **Host Bridge Interface:** `nws-n2n3`
*   **Host IP on this Bridge:** `10.53.1.1`

| Network Function | Component | Interface / Port | IP Address |
|---|---|---|---|
| **AMF** | Access & Mobility Management | **N2** (SCTP / `38412`) | `10.53.1.2` |
| **UPF** | User Plane Function | **N3** GTP-U (UDP / `2152`) | `10.53.1.2` |
| **SMF** | Session Management Function | N4 PFCP (UDP / `8805`) | `10.53.1.2` |
| **UPF** | User Plane Function | N4 PFCP (UDP / `8805`) | `10.53.1.2` |
| **N6** | Core egress to Internet | NAT Gateway | `10.47.0.2` (subnets `10.45.0.0/24`) |


### Running gNB on Host (Local Machine)

If you build and run the OAI gNB directly on the host, update the gNB's `.yaml` configuration file to bind to the host's IP on the bridge and target the Open5GS AMF:

*   **Host Bridge Interface:** `nws-n2n3`
*   **Host IP on this Bridge:** `10.53.1.1` (typically)
*   **Open5GS Core IP (AMF/UPF):** `10.53.1.2`

```yaml
# amf_ip_address points to the nws-5gc container IP
amf_ip_address:
  - ipv4: 10.53.1.2

# NETWORK_INTERFACES points to the host's IP on the nws-n2n3 bridge
NETWORK_INTERFACES:
  GNB_IPV4_ADDRESS_FOR_NG_AMF: 10.53.1.1
  GNB_IPV4_ADDRESS_FOR_NGU: 10.53.1.1
```

### Subscriber Management

Subscribers are defined in `open5gs/subscriber_db.csv`:

```csv
# Format: Name,IMSI,Key,OP_Type,OP/OPc,AMF,QCI,IP_alloc,sst,sd,dnn,session_type
ue01,001010000000001,00112233445566778899aabbccddeeff,opc,63bfa50ee6523365ff14c1f45f88737d,8000,9,10.45.0.2,1,ffffff,oai,1
ue02,001010000000002,00112233445566778899aabbccddef00,opc,63bfa50ee6523365ff14c1f45f88737d,8000,9,10.45.0.3,1,ffffff,oai,1
```

## 🌐 Cross-Platform Compatibility

### Automatic Architecture Detection

The solution automatically detects the host architecture and configures accordingly:

- **ARM64 (aarch64)**: Automatically creates x86_64 symbolic links for compatibility
- **x86_64**: Automatically creates ARM64 symbolic links for compatibility
- **Other architectures**: Fallback configuration with both paths

### Supported Architectures

| Architecture | Status | Notes |
|--------------|--------|-------|
| ARM64 (aarch64) | ✅ Fully Supported | Tested on ARM-based systems |
| x86_64 (amd64) | ✅ Fully Supported | Tested on Intel/AMD systems |
| ARMv7 | ⚠️ Limited | May work with modifications |

## 🔍 Monitoring and Debugging

### Health Checks

```bash
# Check container health
docker compose ps

# View real-time logs
docker compose logs -f 5gc

# Check specific network function
docker exec open5gs_5gc ps aux | grep smf
```

### Debug Mode

Enable debug mode by setting `DEBUG=true` in `open5gs/open5gs.env`:

```bash
# Edit configuration
nano open5gs/open5gs.env

# Set DEBUG=true
DEBUG=true

# Restart container
docker compose restart 5gc
```

### Network Function Status

```bash
# Check all NFs are registered
docker logs open5gs_5gc 2>&1 | grep "NF Profile updated"

# Check SMF status specifically
docker logs open5gs_5gc 2>&1 | grep -E "(SMF|smf)"
```

### iperf3 Server Status

```bash
# Check all iperf3 servers are running
docker exec open5gs_5gc ps aux | grep iperf3

# Test network performance on different ports (from within container)
docker exec open5gs_5gc iperf3 -c localhost -p 5201 -t 10  # Internal server
docker exec open5gs_5gc iperf3 -c localhost -p 5202 -t 10  # N6 interface server
docker exec open5gs_5gc iperf3 -c localhost -p 5000 -t 10  # Additional server 1
docker exec open5gs_5gc iperf3 -c localhost -p 5001 -t 10  # Additional server 2
docker exec open5gs_5gc iperf3 -c localhost -p 5002 -t 10  # Additional server 3
docker exec open5gs_5gc iperf3 -c localhost -p 5003 -t 10  # Additional server 4
```

## 🔧 Advanced Usage

### Custom Configuration

To use custom configuration files:

```bash
# Mount custom config
docker compose run -v /path/to/custom.yml:/open5gs/custom.yml 5gc 5gc -c custom.yml
```

### Scaling and Performance

For production deployments:

```bash
# Increase resources
docker compose up -d --scale 5gc=1

# Monitor resource usage
docker stats open5gs_5gc
```

### Network Customization

To modify network configuration:

1. Edit `docker-compose.yml` network settings
2. Update `open5gs/open5gs.env` IP addresses
3. Restart the container

## 🛠️ Troubleshooting

### Common Issues

#### 1. SMF Not Starting
```bash
# Check architecture detection
docker logs open5gs_5gc 2>&1 | grep "Detected architecture"

# Verify freeDiameter libraries
docker exec open5gs_5gc ls -la /open5gs/install/lib/*/freeDiameter/
```

#### 2. Network Connectivity Issues
```bash
# Check IP forwarding
docker exec open5gs_5gc sysctl net.ipv4.ip_forward

# Verify NAT rules
docker exec open5gs_5gc iptables -t nat -L
```

#### 3. Port Conflicts
```bash
# Check port usage
docker exec open5gs_5gc ss -tlnp | grep -E "(7777|8805|9999)"

# Restart if needed
docker compose restart 5gc
```

### Log Analysis

```bash
# View all logs
docker compose logs 5gc

# Filter specific components
docker compose logs 5gc 2>&1 | grep -E "(ERROR|WARNING|FATAL)"

# Real-time monitoring
docker compose logs -f 5gc | grep -E "(SMF|AMF|UPF)"
```

## 📊 Performance Optimization

### Resource Requirements

| Component | CPU | Memory | Storage |
|-----------|-----|--------|---------|
| **Minimum** | 2 cores | 4GB | 10GB |
| **Recommended** | 4 cores | 8GB | 20GB |
| **Production** | 8+ cores | 16GB+ | 50GB+ |

### Optimization Tips

1. **CPU**: Use more cores for better performance
2. **Memory**: Increase for more concurrent UEs
3. **Storage**: Use SSD for better I/O performance
4. **Network**: Ensure low latency for real-time services

## 🔒 Security Considerations

### Network Security

- All inter-NF communication uses secure protocols
- TLS certificates are automatically generated
- Network isolation between core and external networks

### Access Control

- Web UI access limited to localhost by default
- Database access restricted to container network
- No external ports exposed by default

## 📚 API Documentation

### REST APIs

The 5G Core exposes REST APIs for:

- **NRF**: Service discovery and registration
- **AMF**: UE management and mobility
- **SMF**: Session management
- **PCF**: Policy control

### WebSocket APIs

Real-time notifications available for:
- UE registration events
- Session establishment
- Policy updates

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test on both ARM64 and x86_64
5. Submit a pull request

## 📄 License

This project is licensed under the GNU Affero General Public License v3.0 - see the LICENSE file for details.

## 🆘 Support

### Getting Help

- **Issues**: Create an issue on GitHub
- **Documentation**: Check the Open5GS documentation
- **Community**: Join the Open5GS community forums

### Useful Links

- [Open5GS Official Documentation](https://open5gs.org/)
- [5G Core Network Architecture](https://www.3gpp.org/specifications)
- [Docker Compose Documentation](https://docs.docker.com/compose/)

## 🏆 Acknowledgments

- **Open5GS Project**: For the excellent 5G Core implementation
- **srsRAN Project**: For the gNB implementation
- **Docker Community**: For containerization support

---

**Note**: This solution is designed for development, testing, and educational purposes. For production deployments, additional security, monitoring, and scaling considerations should be implemented.