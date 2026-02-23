# ms-openroad runner image
#
# Base on upstream openroad/orfs runtime image, but overlay this repo's flow
# scripts (so changes to this branch are picked up by cloud builds).

ARG BASE_IMAGE=openroad/orfs:latest
FROM ${BASE_IMAGE}

ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF

WORKDIR /OpenROAD-flow-scripts

# Overlay flow scripts from this repo/branch
COPY env.sh /OpenROAD-flow-scripts/env.sh
# Keep the context small: only copy what's needed for nangate45/gcd.
COPY flow/Makefile /OpenROAD-flow-scripts/flow/Makefile
COPY flow/scripts /OpenROAD-flow-scripts/flow/scripts
COPY flow/util /OpenROAD-flow-scripts/flow/util
COPY flow/designs/src/gcd /OpenROAD-flow-scripts/flow/designs/src/gcd
COPY flow/designs/nangate45/gcd /OpenROAD-flow-scripts/flow/designs/nangate45/gcd
COPY flow/platforms/common /OpenROAD-flow-scripts/flow/platforms/common
COPY flow/platforms/nangate45 /OpenROAD-flow-scripts/flow/platforms/nangate45

COPY ms-openroad/run-orfs.sh /usr/local/bin/run-orfs.sh
COPY ms-openroad/orfs-extra.mk /usr/local/share/orfs-extra.mk

RUN chmod +x /usr/local/bin/run-orfs.sh \
  && echo "${VCS_REF}" >/OpenROAD-flow-scripts/.ms-openroad-revision \
  && chmod o+rw -R /OpenROAD-flow-scripts

ENTRYPOINT ["/usr/local/bin/run-orfs.sh"]
