## <span id="page-38-0"></span>**Configuring Stateful DHCP Externally**

This interface configuration is for a Cisco IOS IPv6 router implementing stateful DHCPv6 on an external DHCP server.

|        | Command or Action                    | Purpose                                       |
|--------|--------------------------------------|-----------------------------------------------|
| Step 1 | enable                               | Enables privileged EXEC mode.                 |
|        | Example:                             | Enter your password if prompted.              |
|        | Device> enable                       |                                               |
|        | configure terminal                   | Enters global configuration mode.             |
| Step 2 |                                      |                                               |
|        | Device# configure terminal           |                                               |
|        | ipv6 unicast-routing                 | Configures the IPv6 for unicasting.           |
| Step 3 |                                      |                                               |
|        | Device(config)# ipv6 unicast-routing |                                               |
|        |                                      | Provides the DNS server option to DHCP        |
|        | dns-server 2001:100:0:1::1           |                                               |
| Step 4 |                                      |                                               |
|        | Device(config-dhcpv6)# dns-server    |                                               |
|        |                                      | Provides the domain name option to DHCP       |
|        | domain-name example.com              |                                               |
| Step 5 |                                      |                                               |
|        | Device(config-dhcpv6)# domain-name   |                                               |
|        | exit                                 | Returns to the previous mode.                 |
| Step 6 |                                      |                                               |
|        | Device(config-dhcpv6)# exit          |                                               |
|        |                                      | Enters the interface mode to configure the    |
|        |                                      | stateful DHCP.                                |
|        | interface vlan 1                     |                                               |
| Step 7 |                                      |                                               |
|        | Device(config)# interface vlan 1     |                                               |
|        | description IPv6-DHCP-Stateful       | Enter description for the stateful IPv6 DHCP. |
| Step 8 |                                      |                                               |
|        | Device(config-if)# description       |                                               |
|        |                                      | Enters the IPv6 address for the stateful IPv6 |
|        | ipv6 address 2001:DB8:0:20::1/64     |                                               |
| Step 9 |                                      |                                               |

