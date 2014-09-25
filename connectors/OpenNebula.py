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

import hashlib
import xmlrpclib
from IM.xmlobject import XMLObject
from IM.uriparse import uriparse
from IM.VirtualMachine import VirtualMachine
from CloudConnector import CloudConnector

from IM.config import Config
from IM.radl.radl import Feature

# clases para parsear el resultado de las llamadas a la API de ONE
class NIC(XMLObject):
		values = ['BRIDGE', 'IP', 'MAC', 'NETWORK', 'VNID']

class OS(XMLObject):
		values = ['BOOT', 'ROOT']

class GRAPHICS(XMLObject):
		values = ['LISTEN', 'TYPE']

class DISK(XMLObject):
		values = ['CLONE','READONLY','SAVE','SOURCE','TARGET' ]

class TEMPLATE(XMLObject):
		values = [ 'CPU', 'MEMORY', 'NAME', 'RANK', 'REQUIREMENTS', 'VMID', 'VCPU' ]
		tuples = { 'GRAPHICS': GRAPHICS, 'OS': OS }
		tuples_lists = { 'DISK': DISK, 'NIC': NIC }
		numeric = [ 'CPU', 'MEMORY', 'VCPU' ]
		noneval = 0

class HISTORY(XMLObject):
		values = ['SEQ', 'HOSTNAME', 'HID', 'STIME', 'ETIME', 'PSTIME', 'PETIME', 'RSTIME', 'RETIME' ,'ESTIME', 'EETIME', 'REASON' ]

class VM(XMLObject):
		STATE_INIT=0
		STATE_PENDING=1
		STATE_HOLD=2
		STATE_ACTIVE=3
		STATE_STOPPED=4
		STATE_SUSPENDED=5
		STATE_DONE=6
		STATE_FAILED=7
		STATE_STR = {'0': 'init', '1': 'pending', '2': 'hold', '3': 'active', '4': 'stopped', '5': 'suspended', '6': 'done', '7': 'failed' }
		LCM_STATE_STR={'0':'init','1':'prologing','2':'booting','3':'running','4':'migrating','5':'saving (stop)','6':'saving (suspend)','7':'saving (migrate)', '8':'prologing (migration)', '9':'prologing (resume)', '10': 'epilog (stop)','11':'epilog', '12':'cancel','13':'failure','14':'delete','15':'unknown'}
		values = [ 'ID','UID','NAME','LAST_POLL','STATE','LCM_STATE','DEPLOY_ID','MEMORY','CPU','NET_TX','NET_RX', 'STIME','ETIME' ]
#		tuples = { 'TEMPLATE': TEMPLATE, 'HISTORY': HISTORY }
		tuples = { 'TEMPLATE': TEMPLATE }
		numeric = [ 'ID', 'UID', 'STATE', 'LCM_STATE', 'STIME','ETIME' ]

class LEASE(XMLObject):
	values = [ 'IP', 'MAC', 'USED' ]

class TEMPLATE_VNET(XMLObject):
	values = [ 'BRIDGE', 'NAME', 'TYPE', 'NETWORK_ADDRESS' ]
	tuples_lists = { 'LEASES': LEASE }

class LEASES(XMLObject):
	tuples_lists = { 'LEASE': LEASE }
	
class RANGE(XMLObject):
	values = [ 'IP_START', 'IP_END' ]
	
class AR(XMLObject):
	values = [ 'IP', 'MAC', 'TYPE', 'ALLOCATED', 'GLOBAL_PREFIX', 'AR_ID' ]
	
class AR_POOL(XMLObject):
	tuples_lists = { 'AR': AR }

class VNET(XMLObject):
	values = [ 'ID', 'UID', 'GID', 'UNAME', 'GNAME', 'NAME', 'TYPE', 'BRIDGE', 'PUBLIC' ]
	tuples = { 'TEMPLATE': TEMPLATE_VNET, 'LEASES': LEASES, 'RANGE': RANGE, 'AR_POOL':AR_POOL }
	
class VNET_POOL(XMLObject):
	tuples_lists = { 'VNET': VNET }

class OpenNebulaCloudConnector(CloudConnector):
	"""
	Cloud Launcher to the OpenNebula platform
	"""
	
	type = "OpenNebula"
	"""str with the name of the provider."""
	
	def concreteSystem(self, radl_system, auth_data):		
		if radl_system.getValue("disk.0.image.url"):
			url = uriparse(radl_system.getValue("disk.0.image.url"))
			protocol = url[0]
			src_host = url[1].split(':')[0]
			# TODO: check the port
			if (protocol == "one") and self.cloud.server == src_host:
				res_system = radl_system.clone()

				res_system.getFeature("cpu.count").operator = "="
				res_system.getFeature("memory.size").operator = "="

				res_system.addFeature(Feature("provider.type", "=", self.type), conflict="other", missing="other")
				res_system.addFeature(Feature("provider.host", "=", self.cloud.server), conflict="other", missing="other")
				res_system.addFeature(Feature("provider.port", "=", self.cloud.port), conflict="other", missing="other")
	
				return [res_system]
			else:
				return []
		else:
			return [radl_system.clone()]
	
	def getSessionID(self, auth_data, hash_password = None):
		"""
		Get the ONE Session ID from the auth data

		Arguments:
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		   - hash_password(bool, optional): specifies if the password must be hashed
		 
		 Returns: str with the Session ID
		"""
		auth = auth_data.getAuthInfo(OpenNebulaCloudConnector.type)
		if auth and 'username' in auth[0] and 'password' in auth[0]:
			passwd = auth[0]['password']
			if hash_password is None:
				one_ver = self.getONEVersion(auth_data)
				if one_ver == "2.0.0" or one_ver == "3.0.0":
					hash_password = True
			if hash_password:
				passwd = hashlib.sha1(passwd.strip()).hexdigest()
			return auth[0]['username'] + ":" + passwd
		else:
			self.logger.error("No correct auth data has been specified to OpenNebula: username and password")
			return None

	def setIPsFromTemplate(self, vm, template):
		"""
		Set the IPs if the VM from the info obtained in the ONE template object

		Arguments:
		   - vm(:py:class:`IM.VirtualMachine`): VM information.
		   - template(:py:class:`TEMPLATE`): ONE Template information. 
		"""
		for i, nic in enumerate(template.NIC):
			vm.info.systems[0].setValue('net_interface.' + str(i) + '.ip',str(nic.IP))

	def updateVMInfo(self, vm, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return (False, "Incorrect auth data")
		
		func_res = server.one.vm.info(session_id, int(vm.id))
		if len(func_res) == 2:
			(success, res_info) = func_res
		elif len(func_res) == 3:
			(success, res_info, err_code) = func_res
		else:
			return [(False, "Error in the one.vm.info return value")]
		
		if success:
			res_vm = VM(res_info)

			# update the state of the VM
			if res_vm.STATE == 3:
				#if res_vm.LCM_STATE == 3:
				if res_vm.LCM_STATE == 3 or res_vm.LCM_STATE == 2:
					res_state = VirtualMachine.RUNNING
				else:
					res_state = VirtualMachine.PENDING
			elif res_vm.STATE < 3 :
				res_state = VirtualMachine.PENDING
			elif res_vm.LCM_STATE == 15:
				res_state = VirtualMachine.UNKNOWN
			elif res_vm.STATE == 7:
				res_state = VirtualMachine.FAILED
			elif res_vm.STATE == 4 or res_vm.STATE == 5:
				res_state = VirtualMachine.STOPPED
			else:
				res_state = VirtualMachine.OFF
			vm.state = res_state

			# currently only update the memory data, as it is the only one that can be changed 
			vm.info.systems[0].setValue('memory.size', int(res_vm.TEMPLATE.MEMORY), "M") 

			# Update network data
			self.setIPsFromTemplate(vm,res_vm.TEMPLATE)

			if res_vm.STIME > 0:
				vm.info.systems[0].setValue('launch_time', res_vm.STIME)

			return (success, vm)
		else:
			return (success, res_info)

	def launch(self, inf, vm_id, radl, requested_radl, num_vm, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return [(False, "Incorrect auth data")]
		
		system = radl.systems[0]
		# Currently ONE plugin only uses user-password credentials
		if system.getValue('disk.0.os.credentials.password'):
			system.delValue('disk.0.os.credentials.private_key')
			system.delValue('disk.0.os.credentials.public_key')
		
		template = self.getONETemplate(radl, auth_data)
		res = []
		i = 0
		while i < num_vm:
			func_res = server.one.vm.allocate(session_id, template)
			if len(func_res) == 2:
				(success, res_id) = func_res
			elif len(func_res) == 3:
				(success, res_id, err_code) = func_res
			else:
				return [(False, "Error in the one.vm.allocate return value")]
				
			if success:
				vm = VirtualMachine(inf, vm_id, str(res_id), self.cloud, radl, requested_radl)
				res.append((success, vm))
			else:
				res.append((success, "ERROR: " + str(res_id)))
			i += 1
		return res

	def finalize(self, vm, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return (False, "Incorrect auth data")
		func_res = server.one.vm.action(session_id, 'finalize', int(vm.id))
		
		if len(func_res) == 1:
			success = True
			err = vm.id
		elif len(func_res) == 2:
			(success, err) = func_res
		elif len(func_res) == 3:
			(success, err, err_code) = func_res
		else:
			return (False, "Error in the one.vm.action return value")

		return (success, err)
		
	def stop(self, vm, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return (False, "Incorrect auth data")
		func_res = server.one.vm.action(session_id, 'suspend', int(vm.id))
		
		if len(func_res) == 1:
			success = True
			err = vm.id
		elif len(func_res) == 2:
			(success, err) = func_res
		elif len(func_res) == 3:
			(success, err, err_code) = func_res
		else:
			return (False, "Error in the one.vm.action return value")

		return (success, err)
		
	def start(self, vm, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return (False, "Incorrect auth data")
		func_res = server.one.vm.action(session_id, 'resume', int(vm.id))
		
		if len(func_res) == 1:
			success = True
			err = vm.id
		elif len(func_res) == 2:
			(success, err) = func_res
		elif len(func_res) == 3:
			(success, err, err_code) = func_res
		else:
			return (False, "Error in the one.vm.action return value")

		return (success, err)


	def getONETemplate(self, radl, auth_data):
		"""
		Get the ONE template to create the VM

		Arguments:
		   - vmi(:py:class:`dict` of str objects): VMI info.
		   - radl(str): RADL document with the VM features to launch.
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		 
		 Returns: str with the ONE template
		"""
		system = radl.systems[0]

		cpu = system.getValue('cpu.count')
		arch = system.getValue('cpu.arch')
		memory = system.getFeature('memory.size').getValue('M')
		name = system.getValue("disk.0.image.name")
		if not name:
			name = "userimage"
		url = uriparse(system.getValue("disk.0.image.url"))
		path = url[2]

		disks = 'DISK = [ IMAGE_ID = "%s" ]' % path[1:]
		cont = 1
		while system.getValue("disk." + str(cont) + ".size") and system.getValue("disk." + str(cont) + ".device"):
			disk_size = system.getFeature("disk." + str(cont) + ".size").getValue('M')
			disk_device = system.getValue("disk." + str(cont) + ".device")
			
			disks += '''
				DISK = [
					TYPE = fs ,
					FORMAT = ext3,
					SIZE = %d,
					TARGET = %s,
					SAVE = no
					]
			''' % (int(disk_size), disk_device)

			cont +=1 
		
		res = '''
			NAME = %s

			CPU = %s
			VCPU = %s
			MEMORY = %s
			OS = [ ARCH = "%s" ]

			%s

			GRAPHICS = [type="vnc",listen="0.0.0.0"]
		''' % (name, cpu, cpu, memory, arch, disks)

		res += self.get_networks_template(radl, auth_data)

		# include the SSH_KEYS
		# It is supported since 3.8 version, (the VM must be prepared with the ONE contextualization script)
		private = system.getValue('disk.0.os.credentials.private_key')
		public = system.getValue('disk.0.os.credentials.public_key')
		if private and public:
			res += '''
			CONTEXT = [
				SSH_PUBLIC_KEY = "%s"
				]
			''' % public

		self.logger.debug("Template: " + res)

		return res

	def getONEVersion(self, auth_data):
		"""
		Get the ONE version

		Arguments:
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		 
		 Returns: str with the ONE version (format: X.X.X)
		"""
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		
		version = "2.0.0"
		methods = server.system.listMethods()
		if "one.system.version" in methods: 
			session_id = self.getSessionID(auth_data, False)
			(success, res_info, err_code) = server.one.system.version(session_id)
			if success:
				version = res_info
			else:
				version = "3.8.0 or Higher"
		else:		
			if "one.acl.info" in methods:
				version = "3.0.0"
				if "one.vm.chmod" in methods:
					version = "3.2.0 to 3.6.0"

		self.logger.debug("OpenNebula version: " + version)
		return version

	def free_address(self, addres_range):
		"""
		Check if there are at least one address free

		Arguments:
		   - leases(:py:class:`AR`): List of AddressRange of a ONE network.
		 
		 Returns: bool, True if there are at least one lease free or False otherwise
		"""
		for ar in addres_range:
			if ar.ALLOCATED == "":
				return True 
		return False
	
	def free_leases(self, leases):
		"""
		Check if there are at least one lease free

		Arguments:
		   - leases(:py:class:`LEASE`): List of leases of a ONE network.
		 
		 Returns: bool, True if there are at least one lease free or False otherwise
		"""
		for lease in leases.LEASE:
			if int(lease.USED) == 0:
				return True 
		return False

	def getONENetworks(self, auth_data):
		"""
		Get the all ONE (public/private) networks

		Arguments:
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		 
		 Returns: a list of tuples (net_name, net_id, is_public) with the name, ID, and boolean specifying if it is a public network of the found network None if not found
		"""
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return None
		func_res = server.one.vnpool.info(session_id, -2, -1, -1)
		
		if len(func_res) == 2:
			(success, info) = func_res
		elif len(func_res) == 3:
			(success, info, err_code) = func_res
		else:
			self.logger.error("Error in the  one.vnpool.info return value")
			return None
		
		if success:
			pool_info = VNET_POOL(info)
		else:
			self.logger.error("Error in the function one.vnpool.info: " + info)
			return None

		res = []
		for net in pool_info.VNET:
			if net.TEMPLATE.NETWORK_ADDRESS:
				ip = net.TEMPLATE.NETWORK_ADDRESS
			elif net.TEMPLATE.LEASES and len(net.TEMPLATE.LEASES) > 0:
				ip = net.TEMPLATE.LEASES[0].IP
			elif net.AR_POOL and net.AR_POOL.AR and len(net.AR_POOL.AR) > 0:
				# This is the case for one 4.8
				if self.free_address(net.AR_POOL.AR):
					ip = net.AR_POOL.AR[0].IP
				else:
					self.logger.warn("The network with IPs like: " + net.AR_POOL.AR[0].IP + " does not have free leases")
					break				
			else:
				self.logger.warn("IP information is not in the VNET POOL. Use the vn.info")
				info_res = server.one.vn.info(session_id, int(net.ID))
				
				if len(info_res) == 2:
					(success, info) = info_res
				elif len(func_res) == 3:
					(success, info, err_code) = info_res
				else:
					self.logger.warn("Error in the one.vn.info return value. Ignoring network: " + net.NAME)
					break
				
				net = VNET(info)
				
				if net.LEASES and net.LEASES.LEASE and len(net.LEASES.LEASE) > 0:
					if self.free_leases(net.LEASES):
						ip = net.LEASES.LEASE[0].IP
					else:
						self.logger.warn("The network with IPs like: " + net.LEASES.LEASE[0].IP + " does not have free leases")
						break
				elif net.RANGE and net.RANGE.IP_START:
					ip = net.RANGE.IP_START
				else:
					self.logger.error("Unknown type of network")
					return (None, None)
			
			is_public = not (ip.startswith("10") or ip.startswith("172") or ip.startswith("169.254") or ip.startswith("192.168")) 

			res.append((net.NAME, net.ID, is_public))
				
		return res

	def map_radl_one_networks(self, radl_nets, one_nets):
		"""
		Generate a mapping between the RADL networks and the ONE networks
	
		Arguments:
		   - radl_nets(list of :py:class:`radl.network` objects): RADL networks.
		   - one_nets(a list of tuples (net_name, net_id, is_public)): ONE networks (returned by getONENetworks function).
		 
		 Returns: a dict with key the RADL network id and value a tuple (one_net_name, one_net_id)
		"""
		res = {}
		
		used_nets = []
		last_net = None
		for radl_net in radl_nets:
			for (net_name, net_id, is_public) in one_nets:
				if net_id not in used_nets and radl_net.isPublic() == is_public :
					res[radl_net.id] = (net_name, net_id)
					used_nets.append(net_id)
					last_net = (net_name, net_id)
					break
			if radl_net.id not in res:
				res[radl_net.id] = last_net
	
		# In case of there are no private network, use public ones for non maped networks
		used_nets = []
		for radl_net in radl_nets:
			if not res[radl_net.id]:
				for (net_name, net_id, is_public) in one_nets:
					if net_id not in used_nets and is_public:
						res[radl_net.id] = (net_name, net_id)
						used_nets.append(net_id)
						last_net = (net_name, net_id)
						break
				if radl_net.id not in res:
					res[radl_net.id] = last_net	
		
		return res


	def get_networks_template(self, radl, auth_data):
		"""
		Generate the network part of the ONE template

		Arguments:
		   - radl(str): RADL document with the VM features to launch.
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		 
		 Returns: str with the network part of the ONE template
		"""
		res = ""
		one_ver = self.getONEVersion(auth_data)

		one_nets = self.getONENetworks(auth_data)
		if not one_nets:
			self.logger.error("No ONE network found")
			return res
		nets = self.map_radl_one_networks(radl.networks, one_nets)

		system = radl.systems[0]
		i = 0
		while system.getValue("net_interface." + str(i) + ".connection"):
			network = system.getValue("net_interface." + str(i) + ".connection")
			fixed_ip = system.getValue("net_interface." + str(i) + ".ip")
			
			# get the one network info
			if nets[network]:
				(net_name, net_id) = nets[network]
			else:
				self.logger.error("No ONE network found for network: " + network)
				raise Exception("No ONE network found for network: " + network)
			
			if net_id is not None:
				if one_ver.startswith("2."):
					res += 'NIC=[ \nNETWORK="' + net_name + '"\n'
				else:
					res += 'NIC=[ \nNETWORK_ID="' + net_id + '"\n'

				if fixed_ip:
					res += ',IP = "' + fixed_ip + '"\n'

				res +=  ']\n'
			else:
				self.logger.error("The net: " + network + " cannot be defined in ONE")

			i += 1
			
		return res

	def checkSetMem(self):
		"""
		Check if the one.vm.setmem function appears in the ONE server
		 
		 Returns: bool, True if the one.vm.setmem function appears in the ONE server or false otherwise
		"""
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		
		methods = server.system.listMethods()
		if "one.vm.setmem" in methods:
			return True
		else:
			return False

	def alterVM(self, vm, radl, auth_data):
		server_url = "http://%s:%d/RPC2" % (self.cloud.server, self.cloud.port)
		server = xmlrpclib.ServerProxy(server_url,allow_none=True)
		session_id = self.getSessionID(auth_data)
		if session_id == None:
			return (False, "Incorrect auth data")
		
		if self.checkSetMem():
			new_mem = radl.getValue('memory.size')
			(success, info, err_code) = server.one.vm.setmem(str(vm.id), int(new_mem))
			
			if success:
				return self.updateVMInfo(vm, auth_data)
			else:
				return (success, info)
		else:
			return (False, "Not supported")
