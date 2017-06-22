# IM - Infrastructure Manager
# Copyright (C) 2011 - GRyCAP - Universitat Politecnica de Valencia
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import random
import time
from ssl import SSLError
import os
import re
import base64
import requests
import tempfile
import uuid
import yaml
from IM.uriparse import uriparse
from IM.VirtualMachine import VirtualMachine
from .CloudConnector import CloudConnector
from radl.radl import Feature
from netaddr import IPNetwork, IPAddress
from IM.config import Config


class OCCICloudConnector(CloudConnector):
    """
    Cloud Launcher to the OCCI platform (FedCloud)
    """

    type = "OCCI"
    """str with the name of the provider."""
    INSTANCE_TYPE = 'small'
    """str with the name of the default instance type to launch."""
    DEFAULT_USER = 'cloudadm'
    """ default user to SSH access the VM """
    MAX_ADD_IP_COUNT = 5
    """ Max number of retries to get a public IP """

    VM_STATE_MAP = {
        'waiting': VirtualMachine.PENDING,
        'active': VirtualMachine.RUNNING,
        'inactive': VirtualMachine.OFF,
        'error': VirtualMachine.FAILED,
        'suspended': VirtualMachine.STOPPED
    }
    """Dictionary with a map with the OCCI VM states to the IM states."""

    def __init__(self, cloud_info, inf):
        self.add_public_ip_count = 0
        CloudConnector.__init__(self, cloud_info, inf)

    @staticmethod
    def create_request_static(method, url, auth, headers, body=None):
        if auth and 'proxy' in auth:
            proxy = auth['proxy']

            (fproxy, proxy_filename) = tempfile.mkstemp()
            os.write(fproxy, proxy.encode())
            os.close(fproxy)
            cert = proxy_filename
        else:
            cert = None
        try:
            resp = requests.request(method, url, verify=False, cert=cert, headers=headers, data=body)
        finally:
            if cert:
                try:
                    os.unlink(cert)
                except:
                    pass

        return resp

    def create_request(self, method, url, auth_data, headers, body=None):
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "%s://%s:%d%s" % (self.cloud.protocol, self.cloud.server, self.cloud.port, url)

        auths = auth_data.getAuthInfo(self.type, self.cloud.server)
        if not auths:
            raise Exception("No correct auth data has been specified to OCCI.")
        else:
            auth = auths[0]

        return self.create_request_static(method, url, auth, headers, body)

    def get_auth_header(self, auth_data):
        """
        Generate the auth header needed to contact with the OCCI server.
        I supports Keystone tokens and basic auth.
        """
        auths = auth_data.getAuthInfo(self.type, self.cloud.server)
        if not auths:
            raise Exception("No correct auth data has been specified to OCCI.")
        else:
            auth = auths[0]

        auth_header = None
        keystone_uri = KeyStoneAuth.get_keystone_uri(self, auth_data)

        if keystone_uri:
            # TODO: Check validity of token
            keystone_token = KeyStoneAuth.get_keystone_token(
                self, keystone_uri, auth)
            auth_header = {'X-Auth-Token': keystone_token}
        else:
            if 'username' in auth and 'password' in auth:
                passwd = auth['password']
                user = auth['username']
                auth_header = {'Authorization': 'Basic ' +
                               (base64.encodestring((user + ':' + passwd).encode('utf-8'))).strip().decode('utf-8')}

        return auth_header

    def concreteSystem(self, radl_system, auth_data):
        image_urls = radl_system.getValue("disk.0.image.url")
        if not image_urls:
            return [radl_system.clone()]
        else:
            if not isinstance(image_urls, list):
                image_urls = [image_urls]

            res = []
            for str_url in image_urls:
                url = uriparse(str_url)
                protocol = url[0]
                cloud_url = self.cloud.protocol + "://" + self.cloud.server + ":" + str(self.cloud.port)
                if (protocol in ['https', 'http'] and url[2] and url[0] + "://" + url[1] == cloud_url):
                    res_system = radl_system.clone()

                    res_system.getFeature("cpu.count").operator = "="
                    res_system.getFeature("memory.size").operator = "="

                    res_system.addFeature(
                        Feature("disk.0.image.url", "=", str_url), conflict="other", missing="other")

                    res_system.addFeature(
                        Feature("provider.type", "=", self.type), conflict="other", missing="other")
                    res_system.addFeature(Feature(
                        "provider.host", "=", self.cloud.server), conflict="other", missing="other")
                    res_system.addFeature(Feature(
                        "provider.port", "=", self.cloud.port), conflict="other", missing="other")

                    username = res_system.getValue(
                        'disk.0.os.credentials.username')
                    if not username:
                        res_system.setValue(
                            'disk.0.os.credentials.username', self.DEFAULT_USER)

                    res.append(res_system)

            return res

    def get_attached_volumes_from_info(self, occi_res):
        """
        Get the attached volumes in VM from the OCCI information returned by the server
        """
        # Link:
        # </storage/0>;rel="http://schemas.ogf.org/occi/infrastructure#storage";self="/link/storagelink/compute_10_disk_0";category="http://schemas.ogf.org/occi/infrastructure#storagelink
        # http://opennebula.org/occi/infrastructure#storagelink";occi.core.id="compute_10_disk_0";occi.core.title="ttylinux
        # -
        # kvm_file0";occi.core.target="/storage/0";occi.core.source="/compute/10";occi.storagelink.deviceid="/dev/hda";occi.storagelink.state="active"
        lines = occi_res.split("\n")
        res = []
        for l in lines:
            if 'Link:' in l and '/storage/' in l:
                num_link = None
                num_storage = None
                device = None
                parts = l.split(';')
                for part in parts:
                    kv = part.split('=')
                    if kv[0].strip() == "self":
                        num_link = kv[1].strip('"')
                    elif kv[0].strip() == "occi.storagelink.deviceid":
                        device = kv[1].strip('"')
                    elif kv[0].strip() == "occi.core.target":
                        num_storage = kv[1].strip('"')
                if num_link and num_storage:
                    res.append((num_link, num_storage, device))
        return res

    def get_net_info(self, occi_res):
        """
        Get the net related information about a VM from the OCCI information returned by the server
        """
        # Link:
        # </network/1>;rel="http://schemas.ogf.org/occi/infrastructure#network";self="/link/networkinterface/compute_10_nic_0";category="http://schemas.ogf.org/occi/infrastructure#networkinterface
        # http://schemas.ogf.org/occi/infrastructure/networkinterface#ipnetworkinterface
        # http://opennebula.org/occi/infrastructure#networkinterface";occi.core.id="compute_10_nic_0";occi.core.title="private";occi.core.target="/network/1";occi.core.source="/compute/10";occi.networkinterface.interface="eth0";occi.networkinterface.mac="10:00:00:00:00:05";occi.networkinterface.state="active";occi.networkinterface.address="10.100.1.5";org.opennebula.networkinterface.bridge="br1"
        lines = occi_res.split("\n")
        res = []
        link_to_public = False
        for l in lines:
            if 'Link:' in l and '/network/public' in l:
                link_to_public = True
            if 'Link:' in l and ('/network/' in l or '/networklink/' in l):
                num_interface = None
                ip_address = None
                parts = l.split(';')
                for part in parts:
                    kv = part.split('=')
                    if kv[0].strip() == "occi.networkinterface.address":
                        ip_address = kv[1].strip('"')
                        is_private = any([IPAddress(ip_address) in IPNetwork(
                            mask) for mask in Config.PRIVATE_NET_MASKS])
                    elif kv[0].strip() == "occi.networkinterface.interface":
                        net_interface = kv[1].strip('"')
                        num_interface = re.findall('\d+', net_interface)[0]
                if num_interface and ip_address:
                    res.append((num_interface, ip_address, not is_private))
        return link_to_public, res

    def setIPs(self, vm, occi_res, auth_data):
        """
        Set to the VM info the IPs obtained from the OCCI info
        """
        public_ips = []
        private_ips = []

        link_to_public, addresses = self.get_net_info(occi_res)
        for _, ip_address, is_public in addresses:
            if is_public:
                public_ips.append(ip_address)
            else:
                private_ips.append(ip_address)

        if (vm.state == VirtualMachine.RUNNING and not link_to_public and
                not public_ips and vm.requested_radl.hasPublicNet(vm.info.systems[0].name)):
            self.log_debug("The VM does not have public IP trying to add one.")
            if self.add_public_ip_count < self.MAX_ADD_IP_COUNT:
                # in some sites the network is called floating, in others PUBLIC ...
                net_names = ["public", "PUBLIC", "floating"]
                msgs = ""
                for name in net_names:
                    success, msg = self.add_public_ip(vm, auth_data, network_name=name)
                    msgs += msg + "\n"
                    if success:
                        break
                if success:
                    self.log_debug("Public IP successfully added.")
                else:
                    self.add_public_ip_count += 1
                    self.log_warn("Error adding public IP the VM: %s (%d/%d)\n" % (msgs,
                                                                                   self.add_public_ip_count,
                                                                                   self.MAX_ADD_IP_COUNT))
                    self.error_messages += "Error adding public IP the VM: %s (%d/%d)\n" % (msgs,
                                                                                            self.add_public_ip_count,
                                                                                            self.MAX_ADD_IP_COUNT)
            else:
                self.log_error("Error adding public IP the VM: Max number of retries reached.")
                self.error_messages += "Error adding public IP the VM: Max number of retries reached.\n"
                # this is a total fail, stop contextualization
                vm.configured = False
                vm.inf.set_configured(False)
                vm.inf.stop()

        vm.setIps(public_ips, private_ips)

    def get_property_from_category(self, occi_res, category, prop_name):
        """
        Get a property of an OCCI category returned by an OCCI server
        """
        lines = occi_res.split("\n")
        for l in lines:
            if l.find('Category: ' + category + ';') != -1:
                for elem in l.split(';'):
                    kv = elem.split('=')
                    if len(kv) == 2:
                        key = kv[0].strip()
                        value = kv[1].strip('"')
                        if key == prop_name:
                            return value
        return None

    def get_floating_pool(self, auth_data):
        """
        Get a random floating pool available (For OpenStack sites with Neutron)
        """
        _, occi_data = self.query_occi(auth_data)
        lines = occi_data.split("\n")
        pools = []
        for l in lines:
            if 'http://schemas.openstack.org/network/floatingippool#' in l:
                for elem in l.split(';'):
                    if elem.startswith('Category: '):
                            pools.append(elem[10:])
        if pools:
            return pools[random.randint(0, len(pools) - 1)]
        else:
            return None

    def add_public_ip(self, vm, auth_data, network_name="public"):
        _, occi_info = self.query_occi(auth_data)
        url = self.get_property_from_category(occi_info, "networkinterface", "location")
        if not url:
            self.log_error("No location for networkinterface category.")
            return (False, "No location for networkinterface category.")

        auth_header = self.get_auth_header(auth_data)
        try:
            net_id = "imnet.%s" % str(uuid.uuid1())

            body = ('Category: networkinterface;scheme="http://schemas.ogf.org/occi/infrastructure#";class="kind";'
                    'location="%s/link/networkinterface/";title="networkinterface link"\n' % self.cloud.path)
            pool_name = self.get_floating_pool(auth_data)
            if pool_name:
                body += ('Category: %s;scheme="http://schemas.openstack.org/network/floatingippool#";'
                         'class="mixin";location="/mixin/%s/";title="%s"' % (pool_name, pool_name, pool_name))
            body += 'X-OCCI-Attribute: occi.core.id="%s"\n' % net_id
            body += 'X-OCCI-Attribute: occi.core.target="%s/network/%s"\n' % (self.cloud.path, network_name)
            body += 'X-OCCI-Attribute: occi.core.source="%s/compute/%s"' % (self.cloud.path, vm.id)

            headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
            if auth_header:
                headers.update(auth_header)
            resp = self.create_request('POST', url, auth_data, headers, body)

            output = str(resp.text)
            if resp.status_code != 201 and resp.status_code != 200:
                return (False, output)
            else:
                self.log_debug("Public IP added from pool %s" % network_name)
                return (True, vm.id)
        except Exception:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server")

    def get_occi_attribute_value(self, occi_res, attr_name):
        """
        Get the value of an OCCI attribute returned by an OCCI server
        """
        lines = occi_res.split("\n")
        for l in lines:
            if l.find('X-OCCI-Attribute: ' + attr_name + '=') != -1:
                return l.split('=')[1].strip('"')
        return None

    def updateVMInfo(self, vm, auth_data):
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)
        try:
            resp = self.create_request('GET', self.cloud.path + "/compute/" + vm.id, auth_data, headers)

            if resp.status_code != 200:
                return (False, resp.reason + "\n" + resp.text)
            else:
                old_state = vm.state
                occi_state = self.get_occi_attribute_value(resp.text, 'occi.compute.state')

                occi_name = self.get_occi_attribute_value(resp.text, 'occi.core.title')
                if occi_name:
                    vm.info.systems[0].setValue('instance_name', occi_name)

                # I have to do that because OCCI returns 'inactive' when a VM is starting
                # to distinguish from the OFF state
                if old_state == VirtualMachine.PENDING and occi_state == 'inactive':
                    vm.state = VirtualMachine.PENDING
                else:
                    vm.state = self.VM_STATE_MAP.get(occi_state, VirtualMachine.UNKNOWN)

                cores = self.get_occi_attribute_value(resp.text, 'occi.compute.cores')
                if cores:
                    vm.info.systems[0].setValue("cpu.count", int(cores))
                memory = self.get_occi_attribute_value(resp.text, 'occi.compute.memory')
                if memory:
                    vm.info.systems[0].setValue("memory.size", int(float(memory)), 'G')

                console_vnc = self.get_occi_attribute_value(resp.text, 'org.openstack.compute.console.vnc')
                if console_vnc:
                    vm.info.systems[0].setValue("console_vnc", console_vnc)

                # Update the network data
                self.setIPs(vm, resp.text, auth_data)

                # Update disks data
                self.set_disk_info(vm, resp.text, auth_data)
                return (True, vm)

        except Exception as ex:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server: " + str(ex))

    def set_disk_info(self, vm, occi_res, auth_data):
        """
        Update the disks info with the actual device assigned by OCCI
        """
        system = vm.info.systems[0]

        for _, num_storage, device in self.get_attached_volumes_from_info(occi_res):
            cont = 1
            while system.getValue("disk." + str(cont) + ".size") and device:
                if os.path.basename(num_storage) == system.getValue("disk." + str(cont) + ".provider_id"):
                    system.setValue("disk." + str(cont) + ".device", device)
                cont += 1

    def gen_cloud_config(self, public_key, user=None, cloud_config_str=None):
        """
        Generate the cloud-config file to be used in the user_data of the OCCI VM
        """
        if not user:
            user = self.DEFAULT_USER
        user_data = {}
        user_data['name'] = user
        user_data['sudo'] = "ALL=(ALL) NOPASSWD:ALL"
        user_data['lock-passwd'] = True
        user_data['ssh-import-id'] = user
        user_data['ssh-authorized-keys'] = [public_key.strip()]
        config_data = {"users": [user_data]}
        if cloud_config_str:
            cloud_config = yaml.load(cloud_config_str)
            # If the client has included the "users" section, append it to the current one
            if 'users' in cloud_config:
                config_data["users"].extend(cloud_config['users'])
                del cloud_config["users"]

            config_data.update(cloud_config)
        config = "#cloud-config\n%s" % yaml.dump(config_data, default_flow_style=False, width=512)
        return config

    def query_occi(self, auth_data):
        """
        Get the info contacting with the OCCI server
        """
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)
        try:
            resp = self.create_request('GET', self.cloud.path + "/-/", auth_data, headers)

            if resp.status_code != 200:
                return False, "Error querying the OCCI server: %s" % resp.reason
            else:
                return True, resp.text
        except Exception as ex:
            return False, "Error querying the OCCI server: %s" % str(ex)

    def get_scheme(self, occi_info, category, ctype):
        """
        Get the scheme of an OCCI category contacting with the OCCI server
        """
        lines = occi_info.split("\n")
        for l in lines:
            if l.find('Category: ' + category) != -1 and l.find(ctype) != -1:
                parts = l.split(';')
                for p in parts:
                    kv = p.split("=")
                    if kv[0].strip() == "scheme":
                        return kv[1].replace('"', '').replace("'", '')

        self.log_error("Error getting scheme for category: " + category)
        return ""

    def get_instance_type_uri(self, occi_info, instance_type):
        """
        Get the whole URI of an OCCI instance from the OCCI info
        """
        if instance_type.startswith('http'):
            # If the user set the whole uri, do not search
            return instance_type
        else:
            return self.get_scheme(occi_info, instance_type, 'resource_tpl') + instance_type

    def get_os_tpl_scheme(self, occi_info, os_tpl):
        """
        Get the whole URI of an OCCI os template from the OCCI info
        """
        return self.get_scheme(occi_info, os_tpl, 'os_tpl')

    def get_cloud_init_data(self, radl):
        """
        Get the cloud init data specified by the user in the RADL
        """
        configure_name = None
        if radl.contextualize.items:
            system_name = radl.systems[0].name

            for item in radl.contextualize.items.values():
                if item.system == system_name and item.get_ctxt_tool() == "cloud_init":
                    configure_name = item.configure

        if configure_name:
            return radl.get_configure_by_name(configure_name).recipes
        else:
            return None

    def create_volumes(self, system, auth_data):
        """
        Attach the required volumes (in the RADL) to the launched instance

        Arguments:
           - instance(:py:class:`boto.ec2.instance`): object to connect to EC2 instance.
           - vm(:py:class:`IM.VirtualMachine`): VM information.
        """
        volumes = []
        cont = 1
        while system.getValue("disk." + str(cont) + ".size"):
            disk_size = system.getFeature(
                "disk." + str(cont) + ".size").getValue('G')
            disk_device = system.getValue("disk." + str(cont) + ".device")
            if disk_device:
                # get the last letter and use vd
                disk_device = "vd" + disk_device[-1]
                system.setValue("disk." + str(cont) + ".device", disk_device)
            self.log_debug("Creating a %d GB volume for the disk %d" % (int(disk_size), cont))
            storage_name = "im-disk-%s" % str(uuid.uuid1())
            success, volume_id = self.create_volume(int(disk_size), storage_name, auth_data)
            if success:
                self.log_debug("Volume id %s sucessfully created." % volume_id)
                volumes.append((disk_device, volume_id))
                system.setValue("disk." + str(cont) + ".provider_id", volume_id)
                # TODO: get the actual device_id from OCCI

                # let's wait the storage to be ready "online"
                wait_ok = self.wait_volume_state(volume_id, auth_data)
                if not wait_ok:
                    self.log_error("Error waiting volume %s. Deleting it." % volume_id)
                    self.delete_volume(volume_id, auth_data)
                    self.error_messages += "Error waiting volume: %s. Deleting it." % volume_id
            else:
                self.log_error("Error creating volume: %s" % volume_id)
                self.error_messages += "Error creating volume: %s. Deleting it." % volume_id

            cont += 1

        return volumes

    def wait_volume_state(self, volume_id, auth_data, wait_state="online", timeout=180, delay=5):
        """
        Wait a storage to be in the specified state (by default "online")
        """
        wait = 0
        online = False
        while not online and wait < timeout:
            # sleep a bit at the beginning to assure a correct state of the vol
            time.sleep(delay)
            wait += delay
            success, storage_info = self.get_volume_info(volume_id, auth_data)
            state = self.get_occi_attribute_value(storage_info, 'occi.storage.state')
            self.log_debug("Waiting volume %s to be %s. Current state: %s" % (volume_id, wait_state, state))
            if success and state == wait_state:
                online = True
            elif not success:
                self.log_error("Error waiting volume %s to be ready: %s" % (volume_id, state))
                return False

        return online

    def get_volume_info(self, storage_id, auth_data):
        """
        Get the OCCI info about the storage
        """
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)
        try:
            resp = self.create_request('GET', self.cloud.path + "/storage/" + storage_id, auth_data, headers)

            if resp.status_code == 404 or resp.status_code == 204:
                return (False, "Volume not found.")
            elif resp.status_code != 200:
                return (False, resp.reason + "\n" + resp.text)
            else:
                return (True, resp.text)
        except Exception as ex:
            self.log_exception("Error getting volume info")
            return False, str(ex)

    def create_volume(self, size, name, auth_data):
        """
        Creates a volume of the specified data (in GB)

        returns the OCCI ID of the storage object
        """
        try:
            auth_header = self.get_auth_header(auth_data)

            volume_id = "im-vol-%s" % str(uuid.uuid1())
            body = 'Category: storage; scheme="http://schemas.ogf.org/occi/infrastructure#"; class="kind"\n'
            body += 'X-OCCI-Attribute: occi.core.id="%s"\n' % volume_id
            body += 'X-OCCI-Attribute: occi.core.title="%s"\n' % name
            body += 'X-OCCI-Attribute: occi.storage.size=%d\n' % int(size)

            headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
            if auth_header:
                headers.update(auth_header)
            resp = self.create_request('POST', self.cloud.path + "/storage/", auth_data, headers, body)

            if resp.status_code != 201 and resp.status_code != 200:
                return False, resp.reason + "\n" + resp.text
            else:
                occi_id = os.path.basename(resp.text)
                return True, occi_id
        except Exception as ex:
            self.log_exception("Error creating volume")
            return False, str(ex)

    def detach_volume(self, volume, auth_data, timeout=180, delay=5):
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)

        link, storage_id, _ = volume
        if not link.startswith("http"):
            link = self.cloud.path + "/" + link

        wait = 0
        while wait < timeout:
            try:
                self.log_debug("Detaching volume: %s" % storage_id)
                resp = self.create_request('DELETE', link, auth_data, headers)
                if resp.status_code in [404, 204, 200]:
                    self.log_debug("Successfully detached")
                    return (True, "")
                else:
                    self.log_error("Error detaching volume: %s" + resp.reason + "\n" + resp.text)
            except Exception as ex:
                self.log_warn("Error detaching volume " + str(ex))

            time.sleep(delay)
            wait += delay

        return (False, "Error detaching the Volume: Timeout.")

    def delete_volume(self, storage_id, auth_data, timeout=180, delay=5):
        """
        Delete a volume
        """
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)

        if storage_id.startswith("http"):
            storage_id = uriparse(storage_id)[2]
        else:
            if not storage_id.startswith("/storage"):
                storage_id = "/storage/%s" % storage_id
            storage_id = self.cloud.path + storage_id

        wait = 0
        while wait < timeout:
            self.log_debug("Delete storage: %s" % storage_id)
            try:
                resp = self.create_request('DELETE', storage_id, auth_data, headers)

                if resp.status_code == 404:
                    self.log_debug("It does not exist.")
                    return (True, "")
                elif resp.status_code == 409:
                    self.log_debug("Error deleting the Volume. It seems that it is still "
                                   "attached to a VM: %s" % resp.text)
                elif resp.status_code != 200 and resp.status_code != 204:
                    self.log_warn("Error deleting the Volume: " + resp.reason + "\n" + resp.text)
                else:
                    self.log_debug("Successfully deleted")
                    return (True, "")
                time.sleep(delay)
                wait += delay
            except Exception:
                self.log_exception("Error connecting with OCCI server")
                return (False, "Error connecting with OCCI server")

        self.log_error("Error deleting the Volume: Timeout")
        return (False, "Error deleting the Volume: Timeout.")

    def launch(self, inf, radl, requested_radl, num_vm, auth_data):
        system = radl.systems[0]
        auth_header = self.get_auth_header(auth_data)

        cpu = system.getValue('cpu.count')
        memory = None
        if system.getFeature('memory.size'):
            memory = system.getFeature('memory.size').getValue('G')
        name = system.getValue("instance_name")
        if not name:
            name = system.getValue("disk.0.image.name")
        if not name:
            name = "im_userimage"
        arch = system.getValue('cpu.arch')

        if arch.find('64'):
            arch = 'x64'
        else:
            arch = 'x86'

        res = []
        i = 0

        public_key = system.getValue('disk.0.os.credentials.public_key')
        # OCCI only uses private key
        if system.getValue('disk.0.os.credentials.password'):
            system.delValue('disk.0.os.credentials.password')

        if not public_key:
            # We must generate them
            (public_key, private_key) = self.keygen()
            system.setValue('disk.0.os.credentials.private_key', private_key)

        user = system.getValue('disk.0.os.credentials.username')
        if not user:
            user = self.DEFAULT_USER
            system.setValue('disk.0.os.credentials.username', user)

        # Add user cloud init data
        cloud_config_str = self.get_cloud_init_data(radl)
        cloud_config = self.gen_cloud_config(public_key, user, cloud_config_str).encode()
        user_data = base64.b64encode(cloud_config).decode().replace("\n", "")
        self.log_debug("Cloud init: %s" % cloud_config.decode())

        # Get the info about the OCCI server (GET /-/)
        success, occi_info = self.query_occi(auth_data)
        if not success:
            raise Exception(occi_info)

        # Parse the info to get the os_tpl scheme
        url = uriparse(system.getValue("disk.0.image.url"))
        # Get the Image ID from the last part of the path
        os_tpl = os.path.basename(url[2])
        os_tpl_scheme = self.get_os_tpl_scheme(occi_info, os_tpl)
        if not os_tpl_scheme:
            raise Exception(
                "Error getting os_tpl scheme. Check that the image specified is supported in the OCCI server.")

        # Parse the info to get the instance_type (resource_tpl) scheme
        instance_type_uri = None
        if system.getValue('instance_type'):
            instance_type = self.get_instance_type_uri(
                occi_info, system.getValue('instance_type'))
            instance_type_uri = uriparse(instance_type)
            if not instance_type_uri[5]:
                raise Exception("Error getting Instance type URI. Check that the instance_type specified is "
                                "supported in the OCCI server.")
            else:
                instance_name = instance_type_uri[5]
                instance_scheme = instance_type_uri[
                    0] + "://" + instance_type_uri[1] + instance_type_uri[2] + "#"

        while i < num_vm:
            volumes = []
            try:
                # First create the volumes
                volumes = self.create_volumes(system, auth_data)

                body = 'Category: compute; scheme="http://schemas.ogf.org/occi/infrastructure#"; class="kind"\n'
                body += 'Category: ' + os_tpl + '; scheme="' + \
                    os_tpl_scheme + '"; class="mixin"\n'
                body += 'Category: user_data; scheme="http://schemas.openstack.org/compute/instance#"; class="mixin"\n'
                # body += 'Category: public_key;
                # scheme="http://schemas.openstack.org/instance/credentials#";
                # class="mixin"\n'

                if instance_type_uri:
                    body += 'Category: ' + instance_name + '; scheme="' + \
                        instance_scheme + '"; class="mixin"\n'
                else:
                    # Try to use this OCCI attributes (not supported by
                    # openstack)
                    if cpu:
                        body += 'X-OCCI-Attribute: occi.compute.cores=' + \
                            str(cpu) + '\n'
                    # body += 'X-OCCI-Attribute: occi.compute.architecture=' + arch +'\n'
                    if memory:
                        body += 'X-OCCI-Attribute: occi.compute.memory=' + \
                            str(memory) + '\n'

                compute_id = "im.%s" % str(uuid.uuid1())
                body += 'X-OCCI-Attribute: occi.core.id="' + compute_id + '"\n'
                body += 'X-OCCI-Attribute: occi.core.title="' + name + '"\n'

                # Set the hostname defined in the RADL
                # Create the VM to get the nodename
                vm = VirtualMachine(inf, None, self.cloud, radl, requested_radl, self)
                (nodename, _) = vm.getRequestedName(default_hostname=Config.DEFAULT_VM_NAME,
                                                    default_domain=Config.DEFAULT_DOMAIN)

                body += 'X-OCCI-Attribute: occi.compute.hostname="' + nodename + '"\n'
                # See: https://wiki.egi.eu/wiki/HOWTO10
                # body += 'X-OCCI-Attribute: org.openstack.credentials.publickey.name="my_key"'
                # body += 'X-OCCI-Attribute: org.openstack.credentials.publickey.data="ssh-rsa BAA...zxe ==user@host"'
                if user_data:
                    body += 'X-OCCI-Attribute: org.openstack.compute.user_data="' + user_data + '"\n'

                # Add volume links
                for device, volume_id in volumes:
                    link_id = "im.%s" % str(uuid.uuid1())
                    body += ('Link: <%s/storage/%s>;rel="http://schemas.ogf.org/occi/infrastructure#storage";'
                             'category="http://schemas.ogf.org/occi/infrastructure#storagelink";'
                             'occi.core.target="%s/storage/%s";'
                             'occi.core.source="%s/compute/%s";'
                             'occi.core.id="%s"' % (self.cloud.path, volume_id,
                                                    self.cloud.path, volume_id,
                                                    self.cloud.path, compute_id, link_id))
                    if device:
                        body += ';occi.storagelink.deviceid="/dev/%s"\n' % device
                    body += '\n'

                self.log_debug(body)

                headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
                if auth_header:
                    headers.update(auth_header)
                resp = self.create_request('POST', self.cloud.path + "/compute/", auth_data, headers, body)

                # some servers return 201 and other 200
                if resp.status_code != 201 and resp.status_code != 200:
                    res.append((False, resp.reason + "\n" + resp.text))
                    for _, volume_id in volumes:
                        self.delete_volume(volume_id, auth_data)
                else:
                    if 'location' in resp.headers:
                        occi_vm_id = os.path.basename(resp.headers['location'])
                    else:
                        occi_vm_id = os.path.basename(resp.text)
                    if occi_vm_id:
                        vm.id = occi_vm_id
                        vm.info.systems[0].setValue('instance_id', str(occi_vm_id))
                        inf.add_vm(vm)
                        res.append((True, vm))
                    else:
                        res.append((False, 'Unknown Error launching the VM.'))

            except Exception as ex:
                self.log_exception("Error connecting with OCCI server")
                res.append((False, "ERROR: " + str(ex)))
                for _, volume_id in volumes:
                    self.delete_volume(volume_id, auth_data)

            i += 1

        return res

    def get_volume_ids_from_radl(self, system):
        volumes = []
        cont = 1
        while system.getValue("disk." + str(cont) + ".size") and system.getValue("disk." + str(cont) + ".device"):
            provider_id = system.getValue("disk." + str(cont) + ".provider_id")
            if provider_id:
                volumes.append(provider_id)
            cont += 1

        return volumes

    def get_attached_volumes(self, vm, auth_data):
        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)
        try:
            resp = self.create_request('GET', self.cloud.path + "/compute/" + vm.id, auth_data, headers)

            if resp.status_code == 404 or resp.status_code == 204:
                return (True, "")
            elif resp.status_code != 200:
                return (False, resp.reason + "\n" + resp.text)
            else:
                occi_volumes = self.get_attached_volumes_from_info(resp.text)
                deleted_vols = []
                for link, num_storage, device in occi_volumes:
                    if device is None or (not device.endswith("vda") and not device.endswith("hda")):
                        deleted_vols.append((link, num_storage, device))
                return (True, deleted_vols)
        except Exception as ex:
            self.log_exception("Error deleting volumes")
            return (False, "Error deleting volumes " + str(ex))

    def finalize(self, vm, last, auth_data):
        # First try to get the volumes
        get_vols_ok, volumes = self.get_attached_volumes(vm, auth_data)
        if not get_vols_ok:
            self.log_error("Error getting attached volumes: %s" % volumes)
        else:
            for volume in volumes:
                self.detach_volume(volume, auth_data)

        auth = self.get_auth_header(auth_data)
        headers = {'Accept': 'text/plain', 'Connection': 'close'}
        if auth:
            headers.update(auth)
        try:
            resp = self.create_request('DELETE', self.cloud.path + "/compute/" + vm.id, auth_data, headers)

            if resp.status_code != 200 and resp.status_code != 404 and resp.status_code != 204:
                return (False, "Error removing the VM: " + resp.reason + "\n" + resp.text)
        except Exception:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server")

        # now delete the volumes
        if get_vols_ok:
            for _, storage_id, _ in volumes:
                self.delete_volume(storage_id, auth_data)

        # sometime we have created a volume that is not correctly attached to the vm
        # check the RADL of the VM to get them
        radl_volumes = self.get_volume_ids_from_radl(vm.info.systems[0])
        for num_storage in radl_volumes:
            self.delete_volume(num_storage, auth_data)

        return (True, vm.id)

    def stop(self, vm, auth_data):
        auth_header = self.get_auth_header(auth_data)
        try:
            headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
            if auth_header:
                headers.update(auth_header)

            body = ('Category: suspend;scheme="http://schemas.ogf.org/occi/infrastructure/compute/action#"'
                    ';class="action";\n')
            resp = self.create_request('POST', self.cloud.path + "/compute/" + vm.id + "?action=suspend",
                                       auth_data, headers, body)

            if resp.status_code not in [200, 204]:
                return (False, "Error stopping the VM: " + resp.reason + "\n" + resp.text)
            else:
                return (True, vm.id)
        except Exception:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server")

    def start(self, vm, auth_data):
        auth_header = self.get_auth_header(auth_data)
        try:
            headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
            if auth_header:
                headers.update(auth_header)

            body = ('Category: start;scheme="http://schemas.ogf.org/occi/infrastructure/compute/action#"'
                    ';class="action";\n')
            resp = self.create_request('POST', self.cloud.path + "/compute/" + vm.id + "?action=start",
                                       auth_data, headers, body)

            if resp.status_code not in [200, 204]:
                return (False, "Error starting the VM: " + resp.reason + "\n" + resp.text)
            else:
                return (True, vm.id)
        except Exception:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server")

    def alterVM(self, vm, radl, auth_data):
        """
        In the OCCI case it only enable to attach new disks
        """
        if not radl.systems:
            return (True, "")

        try:
            orig_system = vm.info.systems[0]

            cont = 1
            while orig_system.getValue("disk." + str(cont) + ".size"):
                cont += 1

            system = radl.systems[0]
            while system.getValue("disk." + str(cont) + ".size"):
                disk_size = system.getFeature("disk." + str(cont) + ".size").getValue('G')
                disk_device = system.getValue("disk." + str(cont) + ".device")
                mount_path = system.getValue("disk." + str(cont) + ".mount_path")
                if disk_device:
                    # get the last letter and use vd
                    disk_device = "vd" + disk_device[-1]
                    system.setValue("disk." + str(cont) + ".device", disk_device)
                self.log_debug("Creating a %d GB volume for the disk %d" % (int(disk_size), cont))
                success, volume_id = self.create_volume(int(disk_size), "im-disk-%d" % cont, auth_data)

                if success:
                    self.log_debug("Volume id %s successfuly created." % volume_id)
                    # let's wait the storage to be ready "online"
                    wait_ok = self.wait_volume_state(volume_id, auth_data)
                    if not wait_ok:
                        self.log_debug("Error waiting volume %s. Deleting it." % volume_id)
                        self.delete_volume(volume_id, auth_data)
                        return (False, "Error waiting volume %s. Deleting it." % volume_id)
                    else:
                        self.log_debug("Attaching to the instance")
                        attached = self.attach_volume(vm, volume_id, disk_device, mount_path, auth_data)
                        if attached:
                            orig_system.setValue("disk." + str(cont) + ".size", disk_size, "G")
                            orig_system.setValue("disk." + str(cont) + ".provider_id", volume_id)
                            if disk_device:
                                orig_system.setValue("disk." + str(cont) + ".device", disk_device)
                            if mount_path:
                                orig_system.setValue("disk." + str(cont) + ".mount_path", mount_path)
                        else:
                            self.log_error("Error attaching a %d GB volume for the disk %d."
                                           " Deleting it." % (int(disk_size), cont))
                            self.delete_volume(volume_id, auth_data)
                            return (False, "Error attaching the new volume")
                else:
                    self.log_error("Error creating volume: %s" % volume_id)
                    return (False, "Error creating volume: %s" % volume_id)

                cont += 1
        except Exception as ex:
            self.log_exception("Error connecting with OCCI server")
            return (False, "Error connecting with OCCI server: " + str(ex))

        return (True, "")

    def attach_volume(self, vm, volume_id, device, mount_path, auth_data):
        """
        Attach a volume to a running VM
        """
        _, occi_info = self.query_occi(auth_data)
        url = self.get_property_from_category(occi_info, "storagelink", "location")
        if not url:
            self.log_error("No location for storagelink category.")
            return (False, "No location for storagelink category.")

        auth_header = self.get_auth_header(auth_data)
        try:
            headers = {'Accept': 'text/plain', 'Connection': 'close', 'Content-Type': 'text/plain,text/occi'}
            if auth_header:
                headers.update(auth_header)

            disk_id = "imdisk.%s" % str(uuid.uuid1())

            body = ('Category: storagelink;scheme="http://schemas.ogf.org/occi/infrastructure#";class="kind";'
                    'location="%s/link/storagelink/";title="storagelink"\n' % self.cloud.path)
            body += 'X-OCCI-Attribute: occi.core.id="%s"\n' % disk_id
            body += 'X-OCCI-Attribute: occi.core.target="%s/storage/%s"\n' % (self.cloud.path, volume_id)
            body += 'X-OCCI-Attribute: occi.core.source="%s/compute/%s"' % (self.cloud.path, vm.id)
            body += 'X-OCCI-Attribute: occi.storagelink.deviceid="/dev/%s"' % device
            # body += 'X-OCCI-Attribute: occi.storagelink.mountpoint="%s"' % mount_path
            resp = self.create_request('POST', url, auth_data, headers, body)

            if resp.status_code != 201 and resp.status_code != 200:
                self.log_error("Error attaching disk to the VM: " + resp.reason + "\n" + resp.text)
                return False
            else:
                return True
        except Exception:
            self.log_exception("Error connecting with OCCI server")
            return False


class KeyStoneAuth:
    """
    Class to manage the Keystone auth tokens used in OpenStack
    """

    @staticmethod
    def get_keystone_uri(occi, auth_data):
        """
        Contact the OCCI server to check if it needs to contact a keystone server.
        It returns the keystone server URI or None.
        """
        try:
            headers = {'Accept': 'text/plain', 'Connection': 'close'}

            resp = occi.create_request('HEAD', occi.cloud.path + "/-/", auth_data, headers)

            www_auth_head = None
            if 'Www-Authenticate' in resp.headers:
                www_auth_head = resp.headers['Www-Authenticate']

            if www_auth_head and www_auth_head.startswith('Keystone uri'):
                return www_auth_head.split('=')[1].replace("'", "")
            else:
                return None
        except SSLError as ex:
            occi.logger.exception(
                "Error with the credentials when contacting with the OCCI server.")
            raise Exception(
                "Error with the credentials when contacting with the OCCI server: %s. Check your proxy file." % str(ex))
        except:
            occi.logger.exception("Error contacting with the OCCI server.")
            return None

    @staticmethod
    def get_keystone_token(occi, keystone_uri, auth):
        """
        Contact the specified keystone server to return the token
        """
        try:
            uri = uriparse(keystone_uri)
            server = uri[1].split(":")[0]
            port = int(uri[1].split(":")[1])

            body = '{"auth":{"voms":true}}'
            headers = {'Accept': 'application/json', 'Connection': 'close', 'Content-Type': 'application/json'}
            url = "https://%s:%s/v2.0/tokens" % (server, port)
            resp = occi.create_request_static('POST', url, auth, headers, body)

            # format: -> "{\"access\": {\"token\": {\"issued_at\":
            # \"2014-12-29T17:10:49.609894\", \"expires\":
            # \"2014-12-30T17:10:49Z\", \"id\":
            # \"c861ab413e844d12a61d09b23dc4fb9c\"}, \"serviceCatalog\": [],
            # \"user\": {\"username\":
            # \"/DC=es/DC=irisgrid/O=upv/CN=miguel-caballer\", \"roles_links\":
            # [], \"id\": \"475ce4978fb042e49ce0391de9bab49b\", \"roles\": [],
            # \"name\": \"/DC=es/DC=irisgrid/O=upv/CN=miguel-caballer\"},
            # \"metadata\": {\"is_admin\": 0, \"roles\": []}}}"
            output = resp.json()
            if 'access' in output:
                token_id = output['access']['token']['id']
            else:
                occi.logger.exception("Error obtaining Keystone Token.")
                raise Exception("Error obtaining Keystone Token: %s" % str(output))

            headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                       'X-Auth-Token': token_id, 'Connection': 'close'}
            url = "https://%s:%s/v2.0/tenants" % (server, port)
            resp = occi.create_request_static('GET', url, auth, headers)

            # format: -> "{\"tenants_links\": [], \"tenants\":
            # [{\"description\": \"egi fedcloud\", \"enabled\": true, \"id\":
            # \"fffd98393bae4bf0acf66237c8f292ad\", \"name\": \"egi\"}]}"
            output = resp.json()

            tenant_token_id = None
            # retry for each available tenant (usually only one)
            for tenant in output['tenants']:
                body = '{"auth":{"voms":true,"tenantName":"' + str(tenant['name']) + '"}}'

                headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                           'X-Auth-Token': token_id, 'Connection': 'close'}
                url = "https://%s:%s/v2.0/tokens" % (server, port)
                resp = occi.create_request_static('POST', url, auth, headers, body)

                # format: -> "{\"access\": {\"token\": {\"issued_at\":
                # \"2014-12-29T17:10:49.609894\", \"expires\":
                # \"2014-12-30T17:10:49Z\", \"id\":
                # \"c861ab413e844d12a61d09b23dc4fb9c\"}, \"serviceCatalog\": [],
                # \"user\": {\"username\":
                # \"/DC=es/DC=irisgrid/O=upv/CN=miguel-caballer\", \"roles_links\":
                # [], \"id\": \"475ce4978fb042e49ce0391de9bab49b\", \"roles\": [],
                # \"name\": \"/DC=es/DC=irisgrid/O=upv/CN=miguel-caballer\"},
                # \"metadata\": {\"is_admin\": 0, \"roles\": []}}}"
                output = resp.json()
                if 'access' in output:
                    tenant_token_id = str(output['access']['token']['id'])
                    break

            return tenant_token_id
        except Exception as ex:
            occi.logger.exception("Error obtaining Keystone Token.")
            raise Exception("Error obtaining Keystone Token: %s" % str(ex))
