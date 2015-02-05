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

import base64
import httplib
import time
import os
import tempfile
from IM.xmlobject import XMLObject
from IM.uriparse import uriparse
from IM.VirtualMachine import VirtualMachine
from CloudConnector import CloudConnector
from IM.radl.radl import UserPassCredential, Feature
from IM.config import Config

# Set of classes to parse the output of the REST API
class Endpoint(XMLObject):
	values = ['Name', 'Vip', 'PublicPort', 'LocalPort', 'Protocol']

class InstanceEndpoints(XMLObject):
	tuples_lists = { 'InstanceEndpoint': Endpoint }

class DataVirtualHardDisk(XMLObject):
	values = ['DiskName', 'Lun']

class DataVirtualHardDisks(XMLObject):
	tuples_lists = { 'DataVirtualHardDisk': DataVirtualHardDisk }

class Role(XMLObject):
	values = ['RoleName', 'RoleType']
	tuples = { 'DataVirtualHardDisks': DataVirtualHardDisks }
	
class RoleList(XMLObject):
	tuples_lists = { 'Role': Role }

class RoleInstance(XMLObject):
	values = ['RoleName', 'InstanceStatus', 'InstanceSize', 'InstanceName', 'IpAddress', 'PowerState']
	tuples = { 'InstanceEndpoints': InstanceEndpoints }

class RoleInstanceList(XMLObject):
	tuples_lists = { 'RoleInstance': RoleInstance }

class Deployment(XMLObject):
	tuples = { 'RoleInstanceList': RoleInstanceList, 'RoleList': RoleList }
	values = ['Name', 'Status', 'Url']

class StorageServiceProperties(XMLObject):
	values = ['Description', 'Location', 'Label', 'Status', 'GeoReplicationEnabled', 'CreationTime', 'GeoPrimaryRegion', 'GeoSecondaryRegion']
	
class StorageService(XMLObject):
	values = ['Url', 'ServiceName']
	tuples = { 'StorageServiceProperties': StorageServiceProperties }



class AzureCloudConnector(CloudConnector):
	"""
	Cloud Launcher to the Azure platform
	"""
	
	type = "Azure"
	INSTANCE_TYPE = 'ExtraSmall'
	AZURE_SERVER = "management.core.windows.net"
	AZURE_PORT = 443
	#STORAGE_NAME = "infmanager"
	STORAGE_NAME = "portalvhdsvbfdd62js3256"
	DEFAULT_LOCATION = "West Europe"
	ROLE_NAME= "IMVMRole"
	
	VM_STATE_MAP = {
		'Running': VirtualMachine.RUNNING,
		'Suspended': VirtualMachine.STOPPED,
		'SuspendedTransitioning': VirtualMachine.STOPPED,
		'RunningTransitioning': VirtualMachine.RUNNING,
		'Starting': VirtualMachine.PENDING,
		'Suspending': VirtualMachine.STOPPED,
		'Deploying': VirtualMachine.PENDING,
		'Deleting': VirtualMachine.OFF,
	}
	
	def concreteSystem(self, radl_system, auth_data):
		if radl_system.getValue("disk.0.image.url"):
			url = uriparse(radl_system.getValue("disk.0.image.url"))
			protocol = url[0]
			if protocol == "azr":
				res_system = radl_system.clone()
				instance_type = self.get_instance_type(res_system)
				if not instance_type:
					self.logger.error("Error launching the VM, no instance type available for the requirements.")
					self.logger.debug(res_system)
					return []
				else:
					res_system.addFeature(Feature("cpu.count", "=", instance_type.num_cpu * instance_type.cores_per_cpu), conflict="other", missing="other")
					res_system.addFeature(Feature("memory.size", "=", instance_type.mem, 'M'), conflict="other", missing="other")
					if instance_type.disks > 0:
						res_system.addFeature(Feature("disks.free_size", "=", instance_type.disks * instance_type.disk_space, 'G'), conflict="other", missing="other")
						for i in range(1,instance_type.disks+1):
							res_system.addFeature(Feature("disk.%d.free_size" % i, "=", instance_type.disk_space, 'G'), conflict="other", missing="other")						
					res_system.addFeature(Feature("price", "=", instance_type.price), conflict="me", missing="other")
					
					res_system.addFeature(Feature("instance_type", "=", instance_type.name), conflict="other", missing="other")
					
					res_system.addFeature(Feature("provider.type", "=", self.type), conflict="other", missing="other")
					
					username = res_system.getValue('disk.0.os.credentials.username')
					if not username:
						res_system.setValue('disk.0.os.credentials.username','azureuser')

					return [res_system]
			else:
				return []
		else:
			return [radl_system.clone()]
	
	def get_azure_vm_create_xml(self, vm, storage_account, radl, num):
		system = radl.systems[0]
		name = system.getValue("disk.0.image.name")
		if not name:
			name = "userimage"
		url = uriparse(system.getValue("disk.0.image.url"))

		label = name + " IM created VM"
		(hostname, _) = vm.getRequestedName(default_hostname = Config.DEFAULT_VM_NAME, default_domain = Config.DEFAULT_DOMAIN)
		
		if not hostname:
			hostname = "AzureNode"

		system.updateNewCredentialValues()
		credentials = system.getCredentials()
		SourceImageName = url[1]
		MediaLink = "https://%s.blob.core.windows.net/vhds/%s.vhd" % (storage_account, SourceImageName)
		instance_type = self.get_instance_type(system)
		
		disks = ""
		cont = 1
		while system.getValue("disk." + str(cont) + ".size") and system.getValue("disk." + str(cont) + ".device"):
			disk_size = system.getFeature("disk." + str(cont) + ".size").getValue('G')
			#disk_device = system.getValue("disk." + str(cont) + ".device")

			disk_name = "datadisk-1-" + str(int(time.time()*100))			
			disks += '''
<DataVirtualHardDisks>
  <DataVirtualHardDisk>
    <HostCaching>ReadWrite</HostCaching> 
    <Lun>%d</Lun>
    <LogicalDiskSizeInGB>%d</LogicalDiskSizeInGB>
    <MediaLink>https://%s.blob.core.windows.net/vhds/%s.vhd</MediaLink>            
  </DataVirtualHardDisk>
</DataVirtualHardDisks>
			''' % (cont, int(disk_size), storage_account, disk_name)

			cont +=1 
		#http://example.blob.core.windows.net/disks/mydatadisk.vhd
			
		# TODO: revisar esto
		if system.getValue("disk.0.os.name") == "windows":
			ConfigurationSet = '''
<ConfigurationSet i:type="WindowsProvisioningConfigurationSet">
  <ConfigurationSetType>WindowsProvisioningConfiguration</ConfigurationSetType>
  <ComputerName>%s</ComputerName>
  <AdminPassword>%s</AdminPassword>
  <AdminUsername>%s</AdminUsername>
  <EnableAutomaticUpdates>true</EnableAutomaticUpdates>
  <ResetPasswordOnFirstLogon>false</ResetPasswordOnFirstLogon>
</ConfigurationSet>
			''' % (hostname, credentials.password, credentials.username)
		else:
			if isinstance(credentials, UserPassCredential):
				ConfigurationSet = '''
	<ConfigurationSet i:type="LinuxProvisioningConfigurationSet">
	  <ConfigurationSetType>LinuxProvisioningConfiguration</ConfigurationSetType>
	  <HostName>%s</HostName>
	  <UserName>%s</UserName>
	  <UserPassword>%s</UserPassword>
	  <DisableSshPasswordAuthentication>false</DisableSshPasswordAuthentication>
	</ConfigurationSet>
				''' % (hostname, credentials.username, credentials.password)
			else:
				ConfigurationSet = '''
	<ConfigurationSet i:type="LinuxProvisioningConfigurationSet">
	  <ConfigurationSetType>LinuxProvisioningConfiguration</ConfigurationSetType>
	  <HostName>%s</HostName>
	  <UserName>%s</UserName>
	  <UserPassword>%s</UserPassword>
	  <DisableSshPasswordAuthentication>true</DisableSshPasswordAuthentication>
	  <SSH>
	    <PublicKeys>
              <PublicKey>
                <FingerPrint>%s</FingerPrint>
                <Path>/home/%s/.ssh/authorized_keys</Path>     
              </PublicKey>
            </PublicKeys>
            <KeyPairs>
              <KeyPair>
                <FingerPrint>%s</FinguerPrint>
                <Path>/home/%s/.ssh/id_rsa</Path>
              </KeyPair>
            </KeyPairs>
          </SSH>
	</ConfigurationSet>
				''' % (hostname, credentials.username, SourceImageName,
					credentials.public_key, credentials.username,
					credentials.public_key, credentials.username)


		res = '''
<Deployment xmlns="http://schemas.microsoft.com/windowsazure" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <Name>%s</Name>
  <DeploymentSlot>Production</DeploymentSlot>
  <Label>%s</Label>
  <RoleList>
    <Role i:type="PersistentVMRole">
      <RoleName>%s</RoleName>
      <OsVersion i:nil="true"/>
      <RoleType>PersistentVMRole</RoleType>
      <ConfigurationSets>
      %s
        <ConfigurationSet i:type="NetworkConfigurationSet">
          <ConfigurationSetType>NetworkConfiguration</ConfigurationSetType>
          <InputEndpoints>
            <InputEndpoint>
              <LocalPort>22</LocalPort>
              <Name>SSH</Name>
              <Port>22</Port>
              <Protocol>TCP</Protocol>
            </InputEndpoint>
          </InputEndpoints>
        </ConfigurationSet>
      </ConfigurationSets>
      %s
      <OSVirtualHardDisk>
        <MediaLink>%s</MediaLink>
        <SourceImageName>%s</SourceImageName>
      </OSVirtualHardDisk>
      <RoleSize>%s</RoleSize> 
    </Role>
  </RoleList>
</Deployment>
		''' % (vm.id, label, self.ROLE_NAME, ConfigurationSet, disks, MediaLink, SourceImageName, instance_type.name)
		
		self.logger.debug("Azure VM Create XML: " + res)

		return res
		
	def get_user_subscription_id(self, auth_data):
		auth = auth_data.getAuthInfo(AzureCloudConnector.type)
		if auth and 'username' in auth[0]:
			return auth[0]['username']
		else:
			return None

	def get_user_cert_data(self, auth_data):
		auth = auth_data.getAuthInfo(AzureCloudConnector.type)
		if auth and 'public_key' in auth[0] and 'private_key' in auth[0]:
			certificate = auth[0]['public_key']
			fd, cert_file = tempfile.mkstemp()
			os.write(fd, certificate)
			os.close(fd)
			os.chmod(cert_file,0644)
			
			private_key = auth[0]['private_key']
			fd, key_file = tempfile.mkstemp()
			os.write(fd, private_key)
			os.close(fd)
			os.chmod(key_file,0600)

			return (cert_file, key_file)
		else:
			return None

	def create_service(self, subscription_id, cert_file, key_file, region):
		service_name = "IM-" + str(int(time.time()*100))
		self.logger.info("Create the service " + service_name + " in region: " + region)
		
		try:

			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, key_file=key_file, cert_file=cert_file)
			uri = "https://%s/%s/services/hostedservices" % (self.AZURE_SERVER,subscription_id)
			service_create_xml = '''
	<CreateHostedService xmlns="http://schemas.microsoft.com/windowsazure">
	  <ServiceName>%s</ServiceName>
	  <Label>%s</Label>
	  <Description>Service %s created by the IM</Description>
	  <Location>%s</Location>
	</CreateHostedService> 
			''' % (service_name, base64.b64encode(service_name), service_name, region )
			conn.request('POST', uri, body = service_create_xml, headers = {'x-ms-version' : '2013-03-01', 'Content-Type' : 'application/xml'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
		except Exception:
			self.logger.exception("Error creating the service")
			return None
		
		if resp.status != 201:
			self.logger.error("Error creating the service: Error code: " + str(resp.status) + ". Msg: " + output)
			return None
		
		return service_name
	
	def delete_service(self, service_name, subscription_id, cert_file, key_file):
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/hostedservices/%s?comp=media" % (subscription_id, service_name)
			conn.request('DELETE', uri, headers = {'x-ms-version' : '2013-08-01'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
		except Exception:
			self.logger.exception("Error deleting the service")
			return False
		
		if resp.status != 200:
			self.logger.error("Error deleting the service: Error Code " + str(resp.status) + ". Msg: " + output)
			return False

		return True
	
	def wait_operation_status(self, request_id, subscription_id, cert_file, key_file, req_status = 200, delay = 2, timeout = 60):
		self.logger.info("Wait the operation: " + request_id + " reach the state " + str(req_status))
		status = 0
		wait = 0
		while status != req_status and wait < timeout:
			time.sleep(delay)
			wait += delay
			try:
				conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
				uri = "/%s/operations/%s" % (subscription_id, request_id)
				conn.request('GET', uri, headers = {'x-ms-version' : '2013-03-01'}) 
				resp = conn.getresponse()
				status = resp.status
				conn.close()
				self.logger.debug("Operation state: " + str(status))
			except Exception:
				self.logger.exception("Error getting the operation state: " + request_id)
		
		if status == req_status:
			return True
		else:
			self.logger.exception("Error waiting the operation")
			return False
	
	def create_storage_account(self, storage_account, subscription_id, cert_file, key_file, region, timeout = 120):
		self.logger.info("Creating the storage account " + storage_account)
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/storageservices" % subscription_id
			storage_create_xml = '''
<CreateStorageServiceInput xmlns="http://schemas.microsoft.com/windowsazure">
  <ServiceName>%s</ServiceName>
  <Description>Storage %s created by the IM</Description>
  <Label>%s</Label>
  <Location>%s</Location>
  <GeoReplicationEnabled>false</GeoReplicationEnabled>
  <ExtendedProperties>
    <ExtendedProperty>
      <Name>AccountCreatedBy</Name>
      <Value>RestAPI</Value>
    </ExtendedProperty>
  </ExtendedProperties>
</CreateStorageServiceInput> 
			''' % (storage_account, storage_account, base64.b64encode(storage_account), region)
			conn.request('POST', uri, body = storage_create_xml, headers = {'x-ms-version' : '2013-03-01', 'Content-Type' : 'application/xml'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
		except Exception:
			self.logger.exception("Error creating the storage account")
			return None
		
		if resp.status != 202:
			self.logger.error("Error creating the storage account: Error code " + str(resp.status) + ". Msg: " + output)
			return None

		request_id = resp.getheader('x-ms-request-id')
		
		# Call to GET OPERATION STATUS until 200 (OK)
		success = self.wait_operation_status(request_id, subscription_id, cert_file, key_file)
		
		# Wait the storage to be "Created"
		status = None
		delay = 2
		wait = 0
		while status != "Created" and wait < timeout:
			storage = self.get_storage_account(storage_account, subscription_id, cert_file, key_file)
			if storage:
				status = storage.Status 
			if status != "Created":
				time.sleep(delay)
				wait += delay

		if success:
			return storage_account
		else:
			self.logger.exception("Error creating the storage account")
			self.delete_storage_account(storage_account, subscription_id, cert_file, key_file)
			return None
	
	def delete_storage_account(self, storage_account, subscription_id, cert_file, key_file):
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/storageservices/%s" % (subscription_id, storage_account)
			conn.request('DELETE', uri, headers = {'x-ms-version' : '2013-03-01'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
		except Exception:
			self.logger.exception("Error deleting the storage account")
			return False
		
		if resp.status != 200:
			self.logger.error("Error deleting the storage account: Error Code " + str(resp.status) + ". Msg: " + output)
			return False

		return True
	
	def get_storage_account(self, storage_account, subscription_id, cert_file, key_file):
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/storageservices/%s" % (subscription_id, storage_account)
			conn.request('GET', uri, headers = {'x-ms-version' : '2013-03-01'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
			if resp.status == 200:
				storage_info = StorageService(output)
				return storage_info.StorageServiceProperties
			elif resp.status == 404:
				self.logger.debug("Storage " + storage_account + " does not exist")
				return None
			else:
				self.logger.warn("Error checking the storage account " + storage_account + ". Msg: " + output)
				return None
		except Exception:
			self.logger.exception("Error checking the storage account")
			return None

	def launch(self, inf, radl, requested_radl, num_vm, auth_data):
		subscription_id = self.get_user_subscription_id(auth_data)
		auth = self.get_user_cert_data(auth_data)
		
		if auth is None or subscription_id is None:
			return [(False, "Incorrect auth data")]
		else:
			cert_file, key_file = auth

		region = self.DEFAULT_LOCATION
		if radl.systems[0].getValue('availability_zone'):
			region = radl.systems[0].getValue('availability_zone')

		res = []
		i = 0
		while i < num_vm:
			try:
				# Create storage account
				storage_account = self.get_storage_account(self.STORAGE_NAME, subscription_id, cert_file, key_file)
				if not storage_account:
					storage_account_name = self.create_storage_account(self.STORAGE_NAME, subscription_id, cert_file, key_file, region)
					if storage_account_name is None:
						res.append((False, "Error creating the storage account"))
				else:
					storage_account_name = self.STORAGE_NAME
					# if the user has specified the region
					if radl.systems[0].getValue('availability_zone'):
						# Check that the region of the storage account is the same of the service
						if region != storage_account.GeoPrimaryRegion:
							res.append((False, "Error creating the service. The specified region"))
					else:
						# Otherwise use the storage account region
						region = storage_account.GeoPrimaryRegion

				# and the service
				service_name = self.create_service(subscription_id, cert_file, key_file, region)
				if service_name is None:
					res.append((False, "Error creating the service"))
					break
				
				self.logger.debug("Creating the VM with id: " + service_name)
				
				# Create the VM to get the nodename
				vm = VirtualMachine(inf, service_name, self.cloud, radl, requested_radl)
				
				# Generate the XML to create the VM
				vm_create_xml = self.get_azure_vm_create_xml(vm, storage_account_name, radl, i)
				
				if vm_create_xml == None:
					self.delete_service(service_name, subscription_id, cert_file, key_file)
					res.append((False, "Incorrect image or auth data"))

				conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
				uri = "/%s/services/hostedservices/%s/deployments" % (subscription_id, service_name)
				conn.request('POST', uri, body = vm_create_xml, headers = {'x-ms-version' : '2013-03-01', 'Content-Type' : 'application/xml'}) 
				resp = conn.getresponse()
				output = resp.read()
				conn.close()
				
				if resp.status != 202:
					self.delete_service(service_name, subscription_id, cert_file, key_file)
					self.logger.error("Error creating the VM: Error Code " + str(resp.status) + ". Msg: " + output)
					res.append((False, "Error creating the VM: Error Code " + str(resp.status) + ". Msg: " + output))
				else:
					#Call the GET OPERATION STATUS until sea 200 (OK)
					request_id = resp.getheader('x-ms-request-id')
					success = self.wait_operation_status(request_id, subscription_id, cert_file, key_file)
					if success:
						res.append((True, vm))
					else:
						self.logger.exception("Error waiting the VM creation")
						res.append((False, "Error waiting the VM creation"))

			except Exception, ex:
				self.logger.exception("Error creating the VM")
				res.append((False, "Error creating the VM: " + str(ex)))
			finally:
				# delete tmp files with certificates 
				os.unlink(cert_file)
				os.unlink(key_file)

			i += 1
		return res

	def get_instance_type(self, system):
		"""
		Get the name of the instance type to launch to EC2

		Arguments:
		   - radl(str): RADL document with the requirements of the VM to get the instance type
		Returns: a str with the name of the instance type to launch to EC2	
		"""
		instance_type_name = system.getValue('instance_type')
		
		cpu = system.getValue('cpu.count')
		cpu_op = system.getFeature('cpu.count').getLogOperator()
		arch = system.getValue('cpu.arch')
		memory = system.getFeature('memory.size').getValue('M')
		memory_op = system.getFeature('memory.size').getLogOperator()
		disk_free = 0
		disk_free_op = ">="
		if system.getValue('disks.free_size'):
			disk_free = system.getFeature('disks.free_size').getValue('G')
			disk_free_op = system.getFeature('memory.size').getLogOperator()
		
		instace_types = AzureInstanceTypes.get_all_instance_types()

		res = None
		for instace_type in instace_types:
			# get the instance type with the lowest price
			if res is None or (instace_type.price <= res.price):
				str_compare = "arch in instace_type.cpu_arch "
				str_compare += " and instace_type.cores_per_cpu * instace_type.num_cpu " + cpu_op + " cpu "
				str_compare += " and instace_type.mem " + memory_op + " memory "
				str_compare += " and instace_type.disks * instace_type.disk_space " + disk_free_op + " disk_free"
				
				#if arch in instace_type.cpu_arch and instace_type.cores_per_cpu * instace_type.num_cpu >= cpu and instace_type.mem >= memory and instace_type.cpu_perf >= performance and instace_type.disks * instace_type.disk_space >= disk_free:
				if eval(str_compare):
					if not instance_type_name or instace_type.name == instance_type_name:
						res = instace_type
		
		if res is None:
			AzureInstanceTypes.get_instance_type_by_name(self.INSTANCE_TYPE)
		else:
			return res
		
	def updateVMInfo(self, vm, auth_data):
		# https://msdn.microsoft.com/en-us/library/azure/ee460804.aspx

		self.logger.debug("Get the VM info with the id: " + vm.id)
		auth = self.get_user_cert_data(auth_data)
		subscription_id = self.get_user_subscription_id(auth_data)
		
		if auth is None or subscription_id is None:
			return [(False, "Incorrect auth data")]
		else:
			cert_file, key_file = auth

		service_name = vm.id
	
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/hostedservices/%s/deploymentslots/Production" % (subscription_id, service_name)
			conn.request('GET', uri, headers = {'x-ms-version' : '2013-03-01'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()			
		except Exception, ex:
			# delete tmp files with certificates 
			os.unlink(cert_file)
			os.unlink(key_file)			
			self.logger.exception("Error getting the VM info: " + vm.id)
			return (False, "Error getting the VM info: " + vm.id + ". " + str(ex))
		
		if resp.status != 200:
			self.logger.error("Error getting the VM info: " + vm.id + ". Error Code: " + str(resp.status) + ". Msg: " + output)
			# delete tmp files with certificates 
			os.unlink(cert_file)
			os.unlink(key_file)
			return (False, "Error getting the VM info: " + vm.id + ". Error Code: " + str(resp.status) + ". Msg: " + output)
		else:
			# delete tmp files with certificates 
			os.unlink(cert_file)
			os.unlink(key_file)

			self.logger.debug("VM info: " + vm.id + " obtained.")
			self.logger.debug(output)
			vm_info = Deployment(output)
			
			self.logger.debug("The VM state is: " + vm_info.Status)
				
			vm.state = self.VM_STATE_MAP.get(vm_info.Status, VirtualMachine.UNKNOWN)
			
			# Update IP info
			self.setIPs(vm,vm_info)
			return (True, vm)

	def setIPs(self, vm, vm_info):
		private_ips = []
		public_ips = []
		
		try:
			role_instance = vm_info.RoleInstanceList.RoleInstance[0]
		except:
			return
		try:
			private_ips.append(role_instance.IpAddress)
		except:
			pass
		try:
			public_ips.append(role_instance.InstanceEndpoints.InstanceEndpoint[0].Vip)
		except:
			pass
		
		vm.setIps(public_ips, private_ips)

	def finalize(self, vm, auth_data):
		self.logger.debug("Terminate VM: " + vm.id)
		subscription_id = self.get_user_subscription_id(auth_data)
		auth = self.get_user_cert_data(auth_data)
		
		if auth is None or subscription_id is None:
			return (False, "Incorrect auth data")
		else:
			cert_file, key_file = auth

		service_name = vm.id
		
		res = (False,'')
		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
	
			uri = "/%s/services/hostedservices/%s/deploymentslots/Production?comp=media" % (subscription_id, service_name)
			conn.request('DELETE', uri, headers = {'x-ms-version' : '2013-08-01'}) 
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
			
			if resp.status != 202:
				self.logger.error("Error deleting VM: " + vm.id + ". Error Code: " + str(resp.status) + ". Msg: " + output)
				res = (False, "Error deleting VM: " + vm.id + ". Error Code: " + str(resp.status) + ". Msg: " + output)
			else:
				self.logger.debug("VM terminated: " + vm.id)
				res = (True, vm.id)
		except Exception, ex:
			self.logger.exception("Error terminating VM: " + vm.id)
			res = (False, "Error terminating VM: " + vm.id + ". " + str(ex))

		request_id = resp.getheader('x-ms-request-id')
		
		# wait to finish the VM deletion. Call GET OPERATION STATUS until 200 (OK)
		self.wait_operation_status(request_id, subscription_id, cert_file, key_file)

		# anyway we must try to delete this
		# self.delete_service(service_name, subscription_id, cert_file, key_file)
		
		# delete tmp files with certificates 
		os.unlink(cert_file)
		os.unlink(key_file)
		
		return res
	
	def call_role_operation(self, op, vm, auth_data):
		subscription_id = self.get_user_subscription_id(auth_data)
		auth = self.get_user_cert_data(auth_data)
		
		if auth is None or subscription_id is None:
			return (False, "Incorrect auth data")
		else:
			cert_file, key_file = auth

		service_name = vm.id

		try:
			conn = httplib.HTTPSConnection(self.AZURE_SERVER, self.AZURE_PORT, cert_file=cert_file, key_file=key_file)
			uri = "/%s/services/hostedservices/%s/deployments/%s/roleinstances/%s/Operations" % (subscription_id, service_name, service_name, self.ROLE_NAME)
			
			conn.request('POST', uri, body = op, headers = {'x-ms-version' : '2013-06-01', 'Content-Type' : 'application/xml'})
			resp = conn.getresponse()
			output = resp.read()
			conn.close()
		except Exception, ex:
			# delete tmp files with certificates 
			os.unlink(cert_file)
			os.unlink(key_file)
			self.logger.exception("Error calling role operation")
			return (False, "Error calling role operation: " + str(ex))

		# delete tmp files with certificates 
		os.unlink(cert_file)
		os.unlink(key_file)

		if resp.status != 202:
			self.logger.error("Error calling role operation: Error Code " + str(resp.status) + ". Msg: " + output)
			return (False, "Error calling role operation: Error Code " + str(resp.status) + ". Msg: " + output)

		return (True, "")
	
	def stop(self, vm, auth_data):
		self.logger.debug("Stop VM: " + vm.id)
		
		op = """<ShutdownRoleOperation xmlns="http://schemas.microsoft.com/windowsazure" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <OperationType>ShutdownRoleOperation</OperationType>
  <PostShutdownAction>StoppedDeallocated</PostShutdownAction>
</ShutdownRoleOperation>"""
		return self.call_role_operation(op, vm, auth_data)			
		
	def start(self, vm, auth_data):
		self.logger.debug("Start VM: " + vm.id)

		op = """<StartRoleOperation xmlns="http://schemas.microsoft.com/windowsazure" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <OperationType>StartRoleOperation</OperationType>
</StartRoleOperation>"""
		return self.call_role_operation(op, vm, auth_data)

class InstanceTypeInfo:
	def __init__(self, name = "", cpu_arch = ["i386"], num_cpu = 1, cores_per_cpu = 1, mem = 0, price = 0, disks = 0, disk_space = 0):
		self.name = name
		self.num_cpu = num_cpu
		self.cores_per_cpu = cores_per_cpu
		self.mem = mem
		self.cpu_arch = cpu_arch
		self.price = price
		self.disks = disks
		self.disk_space = disk_space

class AzureInstanceTypes:
	@staticmethod
	def get_all_instance_types():
		list = []
		
		xsmall = InstanceTypeInfo("ExtraSmall", ["x86_64"], 1, 1, 768, 0.0135, 1, 20)
		list.append(xsmall)
		small = InstanceTypeInfo("Small", ["x86_64"], 1, 1, 1792, 0.0574, 1, 40)
		list.append(small)
		medium = InstanceTypeInfo("Medium", ["x86_64"], 1, 2, 3584, 0.1147, 1, 60)
		list.append(medium)
		large = InstanceTypeInfo("Large", ["x86_64"], 1, 4, 7168, 0.229, 1, 120)
		list.append(large)
		xlarge = InstanceTypeInfo("Extra Large", ["x86_64"], 1, 8, 14336, 0.4588, 1, 240)
		list.append(xlarge)
		a5 = InstanceTypeInfo("A5", ["x86_64"], 1, 2, 14336, 0.2458, 1, 135)
		list.append(a5)
		a6 = InstanceTypeInfo("A6", ["x86_64"], 1, 4, 28672, 0.4916, 1, 285)
		list.append(a6)
		a7 = InstanceTypeInfo("A7", ["x86_64"], 1, 8, 57344, 0.9831, 1, 605)
		list.append(a7)
		
		
		return list

	def get_instance_type_by_name(self, name):
		"""
		Get the Azure instance type with the specified name
		
		Returns: an :py:class:`InstanceTypeInfo` or None if the type is not found
		"""
		for inst_type in self.get_all_instance_types():
			if inst_type.name == name:
				return inst_type
		return None