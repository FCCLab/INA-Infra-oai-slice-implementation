#/*
# * Licensed to the OpenAirInterface (OAI) Software Alliance under one or more
# * contributor license agreements.  See the NOTICE file distributed with
# * this work for additional information regarding copyright ownership.
# * The OpenAirInterface Software Alliance licenses this file to You under
# * the OAI Public License, Version 1.1  (the "License"); you may not use this file
# * except in compliance with the License.
# * You may obtain a copy of the License at
# *
# *      http://www.openairinterface.org/?page_id=698
# *
# * Unless required by applicable law or agreed to in writing, software
# * distributed under the License is distributed on an "AS IS" BASIS,
# * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# * See the License for the specific language governing permissions and
# * limitations under the License.
# *-------------------------------------------------------------------------------
# * For more information about the OpenAirInterface (OAI) Software Alliance:
# *      contact@openairinterface.org
# */
#---------------------------------------------------------------------
#
# Dockerfile for OAI gNB and UE development environment
#   Valid for Ubuntu 22.04
#   Source code will be mounted as volume - build manually in container
#
#---------------------------------------------------------------------

FROM ubuntu:jammy
ARG NEEDED_GIT_PROXY
ARG TARGETARCH
ARG UID=1000
ARG GID=1000
# OAI submodule openair2/E2AP/flexric (see ../openairinterface5g/.gitmodules). When the submodule is not checked out, we shallow-fetch this
# exact gitlink revision (must match `git rev-parse HEAD:openair2/E2AP/flexric` in your openairinterface5g tree when you bump OAI).
ARG FLEXRIC_GIT_URL=https://gitlab.eurecom.fr/mosaic5g/flexric.git
ARG FLEXRIC_GIT_REF=6a595d8b243601b7148f60b5cb2cec47658e58c5
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Paris
ENV BUILD_UHD_FROM_SOURCE=True
ENV UHD_VERSION=4.8.0.0

# Install developer packages (combined for gNB, UE, and FlexRIC)
# Note: FlexRIC requires gcc-10, gcc-12, or gcc-13 (not gcc-11)
# Ubuntu 22.04 comes with gcc-11 by default, so we install gcc-12
RUN apt-get update && \
    apt-get upgrade --yes && \
    apt-get install --yes \
       build-essential \
       gcc-12 \
       g++-12 \
       cmake \
       cmake-curses-gui \
       ninja-build \
       ccache \
       psmisc \
       git \
       xxd \
       libpcre2-dev \
       python3-dev \
       bison \
       flex \
       m4 \
       python3-pip \
       wget \
       pkg-config \
       automake \
       libtool \
       patch \
       zlib1g-dev \
       libreadline-dev \
       libssl-dev \
       libcurl4-openssl-dev \
       libsctp-dev \
       libblas-dev \
       liblapack-dev \
       liblapacke-dev \
       software-properties-common \
       procps \
       libsctp1 \
       libboost-chrono1.74.0 \
       libboost-date-time1.74.0 \
       libboost-filesystem1.74.0 \
       libboost-regex1.74.0 \
       libboost-serialization1.74.0 \
       libboost-thread1.74.0 \
       libboost-system1.74.0 \
       libboost-program-options1.74.0 \
       tzdata \
       liblapacke \
       libblas3 \
       libconfig9 \
       openssl \
       net-tools \
       iperf \
       iperf3 \
       iproute2 \
       iputils-ping \
       gdb \
       python3-six \
       python3-requests \
       libusb-1.0-0 \
       libyaml-cpp-dev \
       sudo && \
    pip3 install --ignore-installed pyyaml && \
    apt-get clean -y && \
    rm -rf /var/lib/apt/lists/*

# Set gcc-12 and g++-12 as default compilers (required for FlexRIC)
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 100 && \
    update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-12 100 && \
    update-alternatives --install /usr/bin/cc cc /usr/bin/gcc-12 100 && \
    update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-12 100

# Install SWIG (required for FlexRIC)
RUN git clone https://github.com/swig/swig.git && \
    cd swig && \
    git checkout release-4.1 && \
    ./autogen.sh && \
    ./configure --prefix=/usr/ && \
    make -j8 && \
    make install && \
    ldconfig && \
    cd .. && \
    rm -rf swig

# Add Tini - A tiny but valid init for containers
ENV TINI_VERSION=v0.19.0
RUN wget https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-${TARGETARCH} -O /tini && chmod +x /tini

# Add yq
ENV YQ_VERSION=v4.2.0
RUN wget https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${TARGETARCH} -O /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq

# Configure git proxy if needed
RUN /bin/bash -c "if [[ -v NEEDED_GIT_PROXY ]]; then git config --global http.proxy $NEEDED_GIT_PROXY; fi"

# Copy only files needed for build_oai -I
# Create directory structure
RUN mkdir -p /workspace/openairinterface5g/cmake_targets/tools

# Copy oaienv, build_oai script, and build_helper
COPY oaienv /workspace/openairinterface5g/
COPY cmake_targets/build_oai /workspace/openairinterface5g/cmake_targets/
COPY cmake_targets/tools/build_helper /workspace/openairinterface5g/cmake_targets/tools/

# Make scripts executable
RUN chmod +x /workspace/openairinterface5g/cmake_targets/build_oai

# Install asn1c and SIMDE via build_oai -I
RUN cd /workspace/openairinterface5g && \
    bash -c "source oaienv && cd cmake_targets && ./build_oai -I"

# Create user with host UID/GID
RUN groupadd -g ${GID} oai || true && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash oai || true

# Set up working directories for gNB, UE, and FlexRIC
RUN mkdir -p /opt/oai-gnb/bin /opt/oai-gnb/etc \
             /opt/oai-nr-ue/bin /opt/oai-nr-ue/etc \
             /opt/oai-flexric/bin /opt/oai-flexric/etc \
             /workspace && \
    chown -R ${UID}:${GID} /opt/oai-gnb /opt/oai-nr-ue /opt/oai-flexric /workspace

# Ensure the ping command is available (should come with iputils-ping, but explicit installation for clarity)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes iputils-ping && \
    apt-get clean -y && \
    rm -rf /var/lib/apt/lists/*

COPY openair2/E2AP/flexric/ /workspace/openairinterface5g/openair2/E2AP/flexric/
RUN if [ ! -f /workspace/openairinterface5g/openair2/E2AP/flexric/CMakeLists.txt ]; then \
      echo "FlexRIC sources missing (submodule not initialized); fetching ${FLEXRIC_GIT_URL} at ${FLEXRIC_GIT_REF}"; \
      rm -rf /workspace/openairinterface5g/openair2/E2AP/flexric && \
      mkdir -p /workspace/openairinterface5g/openair2/E2AP/flexric && \
      git -C /workspace/openairinterface5g/openair2/E2AP/flexric init && \
      git -C /workspace/openairinterface5g/openair2/E2AP/flexric remote add origin "${FLEXRIC_GIT_URL}" && \
      git -C /workspace/openairinterface5g/openair2/E2AP/flexric fetch --depth 1 origin "${FLEXRIC_GIT_REF}" && \
      git -C /workspace/openairinterface5g/openair2/E2AP/flexric checkout FETCH_HEAD; \
    fi
RUN cd /workspace/openairinterface5g/openair2/E2AP/flexric && \
    bash -c "rm -rf build && mkdir -p build && cd build && cmake .. && make -j$(nproc) && make install"

# Set up entrypoint script (will be mounted or created)
WORKDIR /workspace

ENTRYPOINT ["/tini", "-v", "--"]
CMD ["sleep", "infinity"]
