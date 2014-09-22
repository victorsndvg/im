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


import unittest
import sys
from mock import Mock

from IM import CloudInfo
from IM.InfrastructureManager import InfrastructureManager as IM, VirtualMachine
from IM.auth import Authentication
from IM.radl.radl import RADL, system, deploy, Feature, SoftFeatures
from IM.config import Config
from connectors.CloudConnector import CloudConnector

class TestIM(unittest.TestCase):
	def __init__(self, *args):
		unittest.TestCase.__init__(self, *args)

	def setUp(self):

		IM._reinit()
		# Patch save_data
		IM.save_data = staticmethod(lambda: None)

	@staticmethod
	def getAuth(im_users=[], vmrc_users=[], clouds=[]):
		return Authentication([
			{'id': 'im%s'%i, 'type': 'InfrastructureManager', 'username': 'user%s'%i,
			 'password': 'pass%s'%i} for i in im_users ] + [
			{'id': 'vmrc%s'%i, 'type': 'VMRC', 'username': 'vmrcuser%s'%i,
			 'password': 'pass%s'%i, 'host':'hostname'} for i in vmrc_users ] + [
			{'id': 'cloud%s'%i, 'type': c, 'username': 'user%s'%i,
			 'password': 'pass%s'%i} for c, i in clouds ])
		
	def register_cloudconnector(self, name, cloud_connector):
		sys.modules['connectors.' + name] = type('MyConnector', (object,),
		                                         {name + 'CloudConnector': cloud_connector})
		
	def test_inf_creation0(self):
		"""Create infrastructure with empty RADL."""

		auth0 = self.getAuth([0])
		infId = IM.CreateInfrastructure("", auth0)
		IM.DestroyInfrastructure(infId, auth0)

	def test_inf_auth(self):
		"""Create infrastructure with empty RADL."""

		auth0, auth1 = self.getAuth([0]), self.getAuth([1])
		infId0 = IM.CreateInfrastructure("", auth0)
		infId1 = IM.CreateInfrastructure("", auth1)
		with self.assertRaises(Exception) as ex:
			IM.DestroyInfrastructure(infId0, auth1)
		self.assertEqual(str(ex.exception), "Invalid infrastructure ID or access not granted.")
		with self.assertRaises(Exception) as ex:
			IM.DestroyInfrastructure(infId1, auth0)
		self.assertEqual(str(ex.exception), "Invalid infrastructure ID or access not granted.")
		IM.DestroyInfrastructure(infId0, auth0)
		IM.DestroyInfrastructure(infId1, auth1)

	def test_inf_addresources0(self):
		"""Deploy single virtual machines and test reference."""

		cloud = CloudConnector
		
		cl_info = CloudInfo
		cl_info.getCloudConnector = Mock(return_value=cloud)
		
		radl = RADL()
		radl.add(system("s0", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er") ]))
		radl.add(deploy("s0", 1))
		
		vm = VirtualMachine(None, 1234, cl_info, radl, radl)
		cloud.launch = Mock(return_value=[(True, vm)])

		self.register_cloudconnector("Mock", cloud)
		auth0 = self.getAuth([0], [], [("Mock", 0)])
		infId = IM.CreateInfrastructure("", auth0)

		vms = IM.AddResource(infId, str(radl), auth0)
		self.assertEqual(vms, ['0'])

		# Test references
		radl = RADL()
		radl.add(system("s0", reference=True))
		radl.add(deploy("s0", 1))
		vms = IM.AddResource(infId, str(radl), auth0)
		self.assertEqual(vms, ['1'])

		cloud.finalize = Mock(return_value=(True, vm))
		IM.DestroyInfrastructure(infId, auth0)

	def test_inf_addresources1(self):
		"""Deploy n independent virtual machines."""

		n = 100 # Machines to deploy
		Config.MAX_SIMULTANEOUS_LAUNCHES = n/2  # Test the pool
		cl_info = CloudInfo
		cloud = CloudConnector
		cl_info.getCloudConnector = Mock(return_value=cloud)
		radl = RADL()
		radl.add(system("s0", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er") ]))
		radl.add(deploy("s0", n))
		vm = VirtualMachine(None, 1224, cl_info, radl, radl)
		cloud.launch = Mock(return_value=[(True, vm)])
		self.register_cloudconnector("Mock", cloud)
		auth0 = self.getAuth([0], [], [("Mock", 0)])
		infId = IM.CreateInfrastructure("", auth0)
		vms = IM.AddResource(infId, str(radl), auth0)
		self.assertEqual(vms, [ '99' for _ in range(n) ])
		self.assertEqual(cloud.launch.call_count, n)
		for call, _ in cloud.launch.call_args_list:
			self.assertEqual(call[2], 1)
		cloud.finalize = Mock(return_value=(True, vm))
		IM.DestroyInfrastructure(infId, auth0)

	def test_inf_addresources2(self):
		"""Deploy independent virtual machines in two cloud providers."""

		n0, n1 = 2, 5 # Machines to deploy
		radl = RADL()
		radl.add(system("s0", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er") ]))
		radl.add(system("s1", [ Feature("disk.0.image.url", "=", "mock1://wind.ows.suc.kz") ]))
		radl.add(deploy("s0", n0))
		radl.add(deploy("s1", n1))
		
		Config.MAX_SIMULTANEOUS_LAUNCHES = 10
		def concreteSystem(s, cloud_id):
			url = s.getValue("disk.0.image.url")
			return [s.clone()] if url.partition(":")[0] == cloud_id else []
		cloud0 = type("MyMock0", (CloudConnector,object), {})
		cl_info0 = CloudInfo
		cl_info0.getCloudConnector = Mock(return_value=cloud0)
		cloud0.launch = Mock(return_value=[(True, VirtualMachine(None, 0, cl_info0, radl, radl))])
		cloud0.concreteSystem = lambda _0, s, _1: concreteSystem(s, "mock0")
		self.register_cloudconnector("Mock0", cloud0)
		cloud1 = type("MyMock1", (CloudConnector,object), {})
		cl_info1 = CloudInfo
		cl_info1.getCloudConnector = Mock(return_value=cloud1)
		cloud1.launch = Mock(return_value=[(True, VirtualMachine(None, 1, cl_info1, radl, radl))])
		cloud1.concreteSystem = lambda _0, s, _1: concreteSystem(s, "mock1")
		self.register_cloudconnector("Mock1", cloud1)
		auth0 = self.getAuth([0], [], [("Mock0", 0), ("Mock1", 1)])
		infId = IM.CreateInfrastructure("", auth0)
		vms = IM.AddResource(infId, str(radl), auth0)
		self.assertEqual(sorted(vms), ['1' for _ in range(n0)] + ['6' for _ in range(n1)])
		self.assertEqual(cloud0.launch.call_count, n0)
		self.assertEqual(cloud1.launch.call_count, n1)
		for call, _ in cloud0.launch.call_args_list + cloud1.launch.call_args_list:
			self.assertEqual(call[2], 1)
		IM.DestroyInfrastructure(infId, auth0)

	def test_inf_addresources3(self):
		"""Test cloud selection."""

		n0, n1 = 2, 5 # Machines to deploy
		radl = RADL()
		radl.add(system("s0", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er"), SoftFeatures(10, [ Feature("memory.size", "<=", 500) ]) ]))
		radl.add(system("s1", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er"),  SoftFeatures(10, [ Feature("memory.size", ">=", 800) ]) ]))
		radl.add(deploy("s0", n0))
		radl.add(deploy("s1", n1))
		
		Config.MAX_SIMULTANEOUS_LAUNCHES = 10
		def concreteSystem(s, mem):
			return [ system(s.name, [ Feature("memory.size", "=", mem) ]) ]
		cloud0 = type("MyMock0", (CloudConnector,object), {})
		cl_info0 = CloudInfo
		cl_info0.getCloudConnector = Mock(return_value=cloud0)
		cloud0.launch = Mock(return_value=[(True, VirtualMachine(None, 0, cl_info0, radl, radl))])
		cloud0.concreteSystem = lambda _0, s, _1: concreteSystem(s, 500)
		self.register_cloudconnector("Mock0", cloud0)
		cloud1 = type("MyMock1", (CloudConnector,object), {})
		cl_info1 = CloudInfo
		cl_info1.getCloudConnector = Mock(return_value=cloud1)
		cloud1.launch = Mock(return_value=[(True, VirtualMachine(None, 1, cl_info1, radl, radl))])
		cloud1.concreteSystem = lambda _0, s, _1: concreteSystem(s, 1000)
		self.register_cloudconnector("Mock1", cloud1)
		auth0 = self.getAuth([0], [0], [("Mock0", 0), ("Mock1", 1)])
		infId = IM.CreateInfrastructure("", auth0)
		vms = IM.AddResource(infId, str(radl), auth0)
		self.assertEqual(sorted(vms), ['1' for _ in range(n0)] + ['6' for _ in range(n1)])
		self.assertEqual(cloud0.launch.call_count, n0)
		self.assertEqual(cloud1.launch.call_count, n1)
		for call, _ in cloud0.launch.call_args_list + cloud1.launch.call_args_list:
			self.assertEqual(call[2], 1)
		IM.DestroyInfrastructure(infId, auth0)

	def test_inf_cloud_order(self):
		"""Test cloud selection in base of the auth data order."""

		n0, n1 = 1, 1 # Machines to deploy
		radl = RADL()
		radl.add(system("s0", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er"), Feature("cpu.count", "=", 1) ]))
		radl.add(deploy("s0", n0))
		radl.add(system("s1", [ Feature("disk.0.image.url", "=", "mock0://linux.for.ev.er"), Feature("cpu.count", "=", 1) ]))
		radl.add(deploy("s1", n1))
		
		cloud0 = type("MyMock0", (CloudConnector,object), {})
		cl_info0 = CloudInfo
		cl_info0.getCloudConnector = Mock(return_value=cloud0)
		cloud0.launch = Mock(return_value=[(True, VirtualMachine(None, 0, cl_info0, radl, radl))])
		self.register_cloudconnector("Mock0", cloud0)
		cloud1 = type("MyMock1", (CloudConnector,object), {})
		cl_info1 = CloudInfo
		cl_info1.getCloudConnector = Mock(return_value=cloud1)
		cloud1.launch = Mock(return_value=[(True, VirtualMachine(None, 1, cl_info1, radl, radl))])
		self.register_cloudconnector("Mock1", cloud1)
		auth0 = self.getAuth([0], [0], [("Mock0", 0), ("Mock1", 1)])
		infId = IM.CreateInfrastructure(str(radl), auth0)
		self.assertEqual(cloud0.launch.call_count, n0 + n1)
		IM.DestroyInfrastructure(infId, auth0)

if __name__ == "__main__":
	unittest.main()
