#!/usr/bin/env python3

import requests
import json
import logging
import argparse
import sys
import time
from typing import Optional, Dict, Any, Union

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DongleControl")

class DongleClient:
    """Client for interacting with Pegatron 5G Dongle JSON-RPC API"""
    
    def __init__(self, ip: str, endpoint: str = "/fibo/webapi", password: Optional[str] = None):
        self.base_url = f"http://{ip}"
        self.api_url = f"{self.base_url}{endpoint}"
        self.password = password
        self.session = requests.Session()
        self._request_id = 0
        
    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id
        
    def _call_api(self, method: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make a JSON-RPC API call to the router
        
        Args:
            method: API method name (e.g., 'GetSignalStrength')
            params: Optional parameters for the method
            
        Returns:
            Result dict from API response, or None on error
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_request_id()
        }
        
        if params:
            payload["params"] = params
            
        try:
            logger.debug(f"Calling API: {method} with params: {params}")
            response = self.session.post(
                self.api_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                logger.error(f"API error for {method}: {data['error']}")
                return None
                
            return data.get("result")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {method}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response for {method}: {e}")
            return None

    def login(self, password: Optional[str] = None) -> bool:
        """Login to router"""
        pwd = password or self.password
        if not pwd:
            logger.warning("No password provided, attempting anonymous access or skipping login.")
            
        # Try login API if password exists
        if pwd:
            result = self._call_api("Login", {"password": pwd})
            if result is not None:
                logger.info("Login successful")
                return True
            else:
                logger.error("Login failed")
                return False
        
        return True

    def get_signal_strength(self) -> Optional[Dict]:
        return self._call_api("GetSignalStrength")

    def get_network_info(self) -> Optional[Dict]:
        return self._call_api("GetNetworkInfo")

    def get_connection_info(self) -> Optional[Dict]:
        return self._call_api("GetConnectionInfo")

    def get_ca_info(self) -> Optional[Dict]:
        return self._call_api("GetCaInfo")

    def get_device_info(self) -> Optional[Dict]:
        return self._call_api("GetDeviceInfo")

    def get_sim_status(self) -> Optional[Dict]:
        return self._call_api("GetSimStatus")

    def get_wan_info(self) -> Optional[Dict]:
        return self._call_api("GetWANInfo")

    def get_airplane_mode(self) -> Optional[Dict]:
        return self._call_api("GetAirplanMode")
        
    def set_airplane_mode(self, enable: bool) -> Optional[Dict]:
        """Set airplane mode (Status: 1 for on, 0 for off)"""
        return self._call_api("SetAirplanMode", {"Status": 1 if enable else 0})

    def get_profile_list(self) -> Optional[Dict]:
        return self._call_api("GetProfileList")

    def add_profile(self, name: str, apn: str, user: str = "", password: str = "", auth_type: int = 0, pdp_type: int = 0) -> Optional[Dict]:
        """
        Add a new APN profile
        AuthType: 0 (Auto), 1 (PAP), 2 (CHAP), 3 (PAP&CHAP)
        PdpType: 0 (IPv4), 1 (IPv6), 2 (IPv4 & IPv6)
        """
        params = {
            "ProfileName": name,
            "APN": apn,
            "UserName": user,
            "Password": password,
            "AuthType": auth_type,
            "PdpType": pdp_type
        }
        return self._call_api("AddProfile", params)
        
    def set_default_profile(self, profile_index: int) -> Optional[Dict]:
        """Set the default profile by index"""
        return self._call_api("SetDefaultProfile", {"ProfileIndex": profile_index})

    def delete_profile(self, profile_index: int) -> Optional[Dict]:
        """Delete APN profile by index (if firmware supports it)."""
        return self._call_api("DeleteProfile", {"ProfileIndex": profile_index})
        
    def raw_command(self, method: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Execute a raw API command"""
        return self._call_api(method, params)

def print_result(data: Any, format_json: bool = False):
    if data is None:
        print("Error: No data received or operation failed.")
        return
        
    if format_json:
        print(json.dumps(data, indent=2))
    else:
        # Simple pretty print for dictionaries
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"{k}: {v}")
        else:
            print(data)

def main():
    parser = argparse.ArgumentParser(description="Pegatron 5G Dongle Control Script")
    parser.add_argument("--ip", default="192.168.11.1", help="Router IP address")
    parser.add_argument("--endpoint", default="/fibo/webapi", help="API endpoint")
    parser.add_argument("--password", help="Login password")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Metrics commands
    subparsers.add_parser("signal", help="Get Signal Strength")
    subparsers.add_parser("network", help="Get Network Info")
    subparsers.add_parser("connection", help="Get Connection Info")
    subparsers.add_parser("device", help="Get Device Info")
    subparsers.add_parser("sim", help="Get SIM Status")
    subparsers.add_parser("wan", help="Get WAN Info")
    subparsers.add_parser("ca", help="Get Carrier Aggregation Info")
    
    # Cell info custom parser
    subparsers.add_parser("cell", help="Get Cell Information (parsed from Network Info)")

    # Airplane Mode
    airplane_parser = subparsers.add_parser("airplane", help="Get/Set Airplane Mode")
    airplane_parser.add_argument("state", nargs="?", choices=["on", "off"], help="Set mode (empty to get status)")

    # APN Settings
    apn_parser = subparsers.add_parser("apn", help="Manage APN Profiles")
    apn_parser.add_argument("--list", action="store_true", help="List all profiles")
    apn_parser.add_argument("--add", action="store_true", help="Add a new profile")
    apn_parser.add_argument("--name", help="Profile Name")
    apn_parser.add_argument("--apn", help="APN String")
    apn_parser.add_argument("--user", default="", help="Username")
    apn_parser.add_argument("--password", default="", help="Password")
    apn_parser.add_argument("--auth", type=int, default=0, help="Auth Type (0=Auto, 1=PAP, 2=CHAP)")
    apn_parser.add_argument("--ip-type", type=int, default=0, help="IP Type (0=IPv4, 1=IPv6, 2=Dual)")
    apn_parser.add_argument("--set-default", type=int, help="Set default profile by Index")
    apn_parser.add_argument("--delete-index", type=int, help="Delete profile by Index (if supported)")

    # Raw command
    raw_parser = subparsers.add_parser("raw", help="Execute raw JSON-RPC method")
    raw_parser.add_argument("method", help="API Method Name")
    raw_parser.add_argument("params", nargs="?", help="JSON string of parameters (optional)")

    # Discover command
    subparsers.add_parser("discover", help="Try to discover undocumented APIs")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = DongleClient(args.ip, args.endpoint, args.password)
    
    # Attempt login if password provided
    if args.password:
        if not client.login():
            sys.exit(1)

    result = None
    
    if args.command == "signal":
        result = client.get_signal_strength()
    elif args.command == "network":
        result = client.get_network_info()
    elif args.command == "connection":
        result = client.get_connection_info()
    elif args.command == "device":
        result = client.get_device_info()
    elif args.command == "sim":
        result = client.get_sim_status()
    elif args.command == "wan":
        result = client.get_wan_info()
    elif args.command == "ca":
        result = client.get_ca_info()
    elif args.command == "cell":
        # Cell info logic extracted from router_metrics_collector.py
        network = client.get_network_info()
        if network:
            cells = network.get("ListNetworkInfo", [])
            if cells:
                result = cells[0] # Return primary cell
            else:
                logger.warning("No cell info found in network response")
    elif args.command == "airplane":
        if args.state:
            enable = (args.state == "on")
            result = client.set_airplane_mode(enable)
            if result is not None:
                print(f"Airplane mode set to {args.state}")
        else:
            res = client.get_airplane_mode()
            if res:
                status = res.get("Status")
                print(f"Airplane Mode: {'ON' if status == 1 else 'OFF'} (Status: {status})")
            result = res
    elif args.command == "apn":
        if args.list:
            result = client.get_profile_list()
        elif args.delete_index is not None:
            result = client.delete_profile(args.delete_index)
            if result is not None:
                print(f"Profile index {args.delete_index} deleted")
        elif args.add:
            if not args.name or not args.apn:
                print("Error: --name and --apn are required for adding a profile")
                sys.exit(1)
            result = client.add_profile(args.name, args.apn, args.user, args.password, args.auth, args.ip_type)
            if result is not None:
                print("Profile added successfully")
        elif args.set_default is not None:
            result = client.set_default_profile(args.set_default)
            if result is not None:
                print(f"Default profile set to index {args.set_default}")
        else:
            # Default to list
            result = client.get_profile_list()

    elif args.command == "raw":
        params = None
        if args.params:
            try:
                params = json.loads(args.params)
            except json.JSONDecodeError:
                logger.error("Invalid JSON parameters")
                sys.exit(1)
        result = client.raw_command(args.method, params)
    elif args.command == "discover":
        potential_methods = [
            "GetSystemInfo", "GetTime", "GetVersion", "GetWanSettings", 
            "GetApn", "GetUsage", "GetSmsStatus", "GetPinStatus", 
            "GetNetworkMode", "GetLteInfo", "GetNrInfo", "GetBatteryInfo",
            "GetWlanInfo", "GetLanInfo", "Reboot", "GetModel"
        ]
        results = {}
        print(f"Testing {len(potential_methods)} potential API methods...")
        for method in potential_methods:
            print(f"Testing {method}...", end="", flush=True)
            res = client.raw_command(method)
            if res is not None:
                print(" FOUND!")
                results[method] = res
            else:
                print(" -")
        
        print("\nDiscovery Results:")
        print_result(results, args.json)

    print_result(result, args.json)

if __name__ == "__main__":
    main()
