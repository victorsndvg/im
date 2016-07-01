#! /usr/bin/env python
#
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

import sys
import unittest
import os
import logging
import logging.config
from StringIO import StringIO

sys.path.append(".")
sys.path.append("..")
from IM.CloudInfo import CloudInfo
from IM.auth import Authentication
from radl import radl_parse
from IM.VirtualMachine import VirtualMachine
from IM.InfrastructureInfo import InfrastructureInfo
from IM.connectors.Azure import AzureCloudConnector
from mock import patch, MagicMock


def read_file_as_string(file_name):
    tests_path = os.path.dirname(os.path.abspath(__file__))
    abs_file_path = os.path.join(tests_path, file_name)
    return open(abs_file_path, 'r').read()


class TestAzureConnector(unittest.TestCase):
    """
    Class to test the IM connectors
    """

    @classmethod
    def setUpClass(cls):
        cls.last_op = None, None
        cls.log = StringIO()
        ch = logging.StreamHandler(cls.log)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)

        logging.RootLogger.propagate = 0
        logging.root.setLevel(logging.ERROR)

        logger = logging.getLogger('CloudConnector')
        logger.setLevel(logging.DEBUG)
        logger.propagate = 0
        logger.addHandler(ch)

    @classmethod
    def clean_log(cls):
        cls.log = StringIO()

    @staticmethod
    def get_azure_cloud():
        cloud_info = CloudInfo()
        cloud_info.type = "Azure"
        cloud = AzureCloudConnector(cloud_info)
        return cloud

    @patch('httplib.HTTPSConnection')
    def test_10_concrete(self, connection):
        radl_data = """
            network net ()
            system test (
            cpu.arch='x86_64' and
            cpu.count>=1 and
            memory.size>=512m and
            net_interface.0.connection = 'net' and
            net_interface.0.dns_name = 'test' and
            disk.0.os.name = 'linux' and
            disk.0.image.url = 'azr://image-id' and
            disk.0.os.credentials.username = 'user'
            )"""
        radl = radl_parse.parse_radl(radl_data)
        radl_system = radl.systems[0]

        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        concrete = azure_cloud.concreteSystem(radl_system, auth)
        self.assertEqual(len(concrete), 1)
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    def get_response(self):
        method, url = self.__class__.last_op

        resp = MagicMock()

        if method == "GET":
            if "/deployments/" in url:
                resp.status = 200
                resp.read.return_value = ("<Deployment><Status>Running</Status><RoleInstanceList><RoleInstance>"
                                          "<InstanceSize>RoleSizeName</InstanceSize><PowerState>Started</PowerState>"
                                          "<IpAddress>10.0.0.1</IpAddress><InstanceEndpoints><InstanceEndpoint>"
                                          "<Vip>158.42.1.1</Vip></InstanceEndpoint></InstanceEndpoints></RoleInstance>"
                                          "</RoleInstanceList></Deployment>")
            if "/operations/" in url:
                resp.status = 200
                resp.read.return_value = ("<Operation><Status>Succeeded"
                                          "</Status></Operation>")
            elif "/storageservices/" in url:
                resp.status = 200
                resp.read.return_value = ("<StorageService><StorageServiceProperties><GeoPrimaryRegion>North Europe"
                                          "</GeoPrimaryRegion></StorageServiceProperties></StorageService>")
            elif url.endswith("/rolesizes"):
                resp.status = 200
                resp.read.return_value = ("<RoleSizes><RoleSize><SupportedByVirtualMachines>true"
                                          "</SupportedByVirtualMachines><Name>RoleSizeName</Name>"
                                          "<MemoryInMb>512</MemoryInMb><Cores>1</Cores>"
                                          "<VirtualMachineResourceDiskSizeInMb>2014"
                                          "</VirtualMachineResourceDiskSizeInMb>"
                                          "</RoleSize>"
                                          "<RoleSize><SupportedByVirtualMachines>true"
                                          "</SupportedByVirtualMachines><Name>RoleSizeName</Name>"
                                          "<MemoryInMb>2048</MemoryInMb><Cores>2</Cores>"
                                          "<VirtualMachineResourceDiskSizeInMb>2014"
                                          "</VirtualMachineResourceDiskSizeInMb>"
                                          "</RoleSize>"
                                          "</RoleSizes>")

        elif method == "POST":
            if url.endswith("/Operations"):
                resp.status = 202
                resp.getheader.return_value = "id"
            elif url.endswith("/services/hostedservices"):
                resp.status = 201
                resp.read.return_value = ""
            elif url.endswith("/deployments"):
                resp.status = 202
                resp.getheader.return_value = "id"
        elif method == "DELETE":
            if url.endswith("comp=media"):
                resp.status = 202
                resp.getheader.return_value = "id"
        elif method == "PUT":
            if "roles" in url:
                resp.status = 202
                resp.getheader.return_value = "id"

        return resp

    def request(self, method, url, body=None, headers={}):
        self.__class__.last_op = method, url

    @patch('httplib.HTTPSConnection')
    @patch('time.sleep')
    def test_20_launch(self, sleep, connection):
        radl_data = """
            network net1 (outbound = 'yes' and outports = '8080')
            network net2 ()
            system test (
            cpu.arch='x86_64' and
            cpu.count>=1 and
            memory.size>=512m and
            net_interface.0.connection = 'net1' and
            net_interface.0.dns_name = 'test' and
            net_interface.1.connection = 'net2' and
            disk.0.os.name = 'linux' and
            disk.0.image.url = 'azr://image-id' and
            disk.0.os.credentials.username = 'user' and
            disk.1.size=1GB and
            disk.1.device='hdb' and
            disk.1.mount_path='/mnt/path'
            )"""
        radl = radl_parse.parse_radl(radl_data)
        radl.check()

        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        res = azure_cloud.launch(InfrastructureInfo(), radl, radl, 1, auth)
        success, _ = res[0]
        self.assertTrue(success, msg="ERROR: launching a VM.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    @patch('httplib.HTTPSConnection')
    def test_30_updateVMInfo(self, connection):
        radl_data = """
            network net (outbound = 'yes')
            system test (
            cpu.arch='x86_64' and
            cpu.count=1 and
            memory.size=512m and
            net_interface.0.connection = 'net' and
            net_interface.0.dns_name = 'test' and
            disk.0.os.name = 'linux' and
            disk.0.image.url = 'azr://image-id' and
            disk.0.os.credentials.username = 'user' and
            disk.0.os.credentials.password = 'pass'
            )"""
        radl = radl_parse.parse_radl(radl_data)
        radl.check()

        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        inf = MagicMock()
        inf.get_next_vm_id.return_value = 1
        vm = VirtualMachine(inf, "1", azure_cloud.cloud, radl, radl, azure_cloud)

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        success, vm = azure_cloud.updateVMInfo(vm, auth)

        self.assertTrue(success, msg="ERROR: updating VM info.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    @patch('httplib.HTTPSConnection')
    @patch('time.sleep')
    def test_40_stop(self, sleep, connection):
        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        inf = MagicMock()
        inf.get_next_vm_id.return_value = 1
        vm = VirtualMachine(inf, "1", azure_cloud.cloud, "", "", azure_cloud)

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        success, _ = azure_cloud.stop(vm, auth)

        self.assertTrue(success, msg="ERROR: stopping VM info.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    @patch('httplib.HTTPSConnection')
    @patch('time.sleep')
    def test_50_start(self, sleep, connection):
        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        inf = MagicMock()
        inf.get_next_vm_id.return_value = 1
        vm = VirtualMachine(inf, "1", azure_cloud.cloud, "", "", azure_cloud)

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        success, _ = azure_cloud.start(vm, auth)

        self.assertTrue(success, msg="ERROR: stopping VM info.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    @patch('httplib.HTTPSConnection')
    @patch('time.sleep')
    def test_55_alter(self, sleep, connection):
        radl_data = """
            network net (outbound = 'yes')
            system test (
            cpu.arch='x86_64' and
            cpu.count=1 and
            memory.size=512m and
            net_interface.0.connection = 'net' and
            net_interface.0.dns_name = 'test' and
            disk.0.os.name = 'linux' and
            disk.0.image.url = 'azr://image-id' and
            disk.0.os.credentials.username = 'user' and
            disk.0.os.credentials.password = 'pass'
            )"""
        radl = radl_parse.parse_radl(radl_data)

        new_radl_data = """
            system test (
            cpu.count>=2 and
            memory.size>=2048m
            )"""
        new_radl = radl_parse.parse_radl(new_radl_data)

        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        inf = MagicMock()
        inf.get_next_vm_id.return_value = 1
        vm = VirtualMachine(inf, "1", azure_cloud.cloud, radl, radl, azure_cloud)

        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        success, _ = azure_cloud.alterVM(vm, new_radl, auth)

        self.assertTrue(success, msg="ERROR: modifying VM info.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()

    @patch('httplib.HTTPSConnection')
    @patch('time.sleep')
    def test_60_finalize(self, sleep, connection):
        auth = Authentication([{'id': 'azure', 'type': 'Azure', 'username': 'user',
                                'public_key': 'public_key', 'private_key': 'private_key'}])
        azure_cloud = self.get_azure_cloud()

        inf = MagicMock()
        inf.get_next_vm_id.return_value = 1
        vm = VirtualMachine(inf, "1", azure_cloud.cloud, "", "", azure_cloud)

        sleep.return_value = True
        conn = MagicMock()
        connection.return_value = conn

        conn.request.side_effect = self.request
        conn.getresponse.side_effect = self.get_response

        success, _ = azure_cloud.finalize(vm, auth)

        self.assertTrue(success, msg="ERROR: finalizing VM info.")
        self.assertNotIn("ERROR", self.log.getvalue(), msg="ERROR found in log: %s" % self.log.getvalue())
        self.clean_log()


if __name__ == '__main__':
    unittest.main()