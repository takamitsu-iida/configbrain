## <span id="page-38-0"></span>**Configuring Stateful DHCP Externally**

This interface configuration is for a Cisco IOS IPv6 router implementing stateful DHCPv6 on an external DHCP server.

### Procedure

#### Step 1

Command or Action: `enable`

Purpose: Enables privileged EXEC mode. Enter your password if prompted.

Example:

```console
Device> enable
```

#### Step 2

Command or Action: `configure terminal`

Purpose: Enters global configuration mode.

Example:

```console
Device# configure terminal
```

#### Step 3

Command or Action: `ipv6 unicast-routing`

Purpose: Configures the IPv6 for unicasting.

Example:

```console
Device(config)# ipv6 unicast-routing
```

#### Step 4

Command or Action: `dns-server 2001:100:0:1::1`

Purpose: Provides the DNS server option to DHCP clients.

Example:

```console
Device(config-dhcpv6)# dns-server 2001:100:0:1::1
```

#### Step 5

Command or Action: `domain-name example.com`

Purpose: Provides the domain name option to DHCP clients.

Example:

```console
Device(config-dhcpv6)# domain-name example.com
```

#### Step 6

Command or Action: `exit`

Purpose: Returns to the previous mode.

Example:

```console
Device(config-dhcpv6)# exit
```

#### Step 7

Command or Action: `interface vlan 1`

Purpose: Enters the interface mode to configure the stateful DHCP.

Example:

```console
Device(config)# interface vlan 1
```

#### Step 8

Command or Action: `description IPv6-DHCP-Stateful`

Purpose: Enter description for the stateful IPv6 DHCP.

Example:

```console
Device(config-if)# description IPv6-DHCP-Stateful
```

#### Step 9

Command or Action: `ipv6 address 2001:DB8:0:20::1/64`

Purpose: Enters the IPv6 address for the stateful IPv6 DHCP.

Example:

```console
Device(config-if)# ipv6 address 2001:DB8:0:20::1/64
```

#### Step 10

Command or Action: `ip address 192.168.20.1 255.255.255.0`

Purpose: Enters the IPv6 address for the stateful IPv6 DHCP.

Example:

```console
Device(config-if)# ip address 192.168.20.1 255.255.255.0
```

#### Step 11

Command or Action: `ipv6 nd prefix 2001:db8::/64 no-advertise`

Purpose: Configures the IPv6 routing prefix advertisement that must not be advertised.

Example:

```console
Device(config-if)# ipv6 nd prefix 2001:db8::/64 no-advertise
```

#### Step 12

Command or Action: `ipv6 nd managed-config-flag`

Purpose: Configures IPv6 interfaces neighbor discovery to allow the hosts to uses DHCP for address configuration.

Example:

```console
Device(config-if)# ipv6 nd managed-config-flag
```

#### Step 13

Command or Action: `ipv6 nd other-config-flag`

Purpose: Configures IPv6 interfaces neighbor discovery to allow the hosts to uses DHCP for non-address configuration.

Example:

```console
Device(config-if)# ipv6 nd other-config-flag
```

#### Step 14

Command or Action: `ipv6 dhcp relay destination 2001:DB8:0:20::2`

Purpose: Configures the DHCP server on the interface.

Example:

```console
Device(config-if)# ipv6 dhcp relay destination 2001:DB8:0:20::2
```

