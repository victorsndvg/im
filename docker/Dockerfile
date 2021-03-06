# Dockerfile to create a container with the IM service
FROM ubuntu:18.04
LABEL maintainer="Miguel Caballer <micafer1@upv.es>"
LABEL version="1.7.0"
LABEL description="Container image to run the IM service. (http://www.grycap.upv.es/im)"
EXPOSE 8899 8800

# Ensure system is up to date with mandatory python packages installed
RUN apt-get update && apt-get install --no-install-recommends -y python-dbg python-dev python-pip openssh-client sshpass vim && rm -rf /var/lib/apt/lists/*

RUN pip install setuptools pip --upgrade -I
RUN pip install pyOpenSSL --upgrade -I

# Install pip optional libraries
RUN pip install msrest msrestazure azure-common azure-mgmt-storage azure-mgmt-compute azure-mgmt-network azure-mgmt-resource azure-mgmt-dns cheroot xmltodict

# Install IM
RUN apt-get update && apt-get install --no-install-recommends -y gcc libmysqld-dev libssl-dev libffi-dev libsqlite3-dev libmysqlclient20 && \
     pip install pycrypto && \
     pip install MySQL-python && \
     pip install IM==1.7.0 && \
     apt-get purge -y gcc libmysqld-dev libssl-dev libffi-dev libsqlite3-dev python-dev python-pip && \
     apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && rm -rf ~/.cache/

# Force requests to be version 2.11.1 to avoid SSL ca errors with proxy files
RUN pip install requests==2.11.1

# Copy a ansible.cfg with correct minimum values
COPY ansible.cfg /etc/ansible/ansible.cfg

# Start IM service
CMD im_service.py