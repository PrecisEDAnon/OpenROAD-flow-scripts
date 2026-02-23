# ms-openroad runner image
#
# Base on upstream openroad/orfs runtime image, but overlay this repo's flow
# scripts (so changes to this branch are picked up by cloud builds).

ARG BASE_IMAGE=openroad/orfs:latest
FROM ${BASE_IMAGE}

WORKDIR /OpenROAD-flow-scripts

# Overlay flow scripts from this repo/branch
COPY env.sh /OpenROAD-flow-scripts/env.sh
COPY flow /OpenROAD-flow-scripts/flow

COPY ms-openroad/run-orfs.sh /usr/local/bin/run-orfs.sh
COPY ms-openroad/orfs-extra.mk /usr/local/share/orfs-extra.mk

RUN chmod +x /usr/local/bin/run-orfs.sh \
  && chmod o+rw -R /OpenROAD-flow-scripts

ENTRYPOINT ["/usr/local/bin/run-orfs.sh"]
