export DESIGN_NICKNAME = bsg_chip
export DESIGN_NAME = bsg_chip
export PLATFORM    = nangate45

TESTCASE_DIR := $(abspath $(dir $(DESIGN_CONFIG)))
export SDC_FILE = $(TESTCASE_DIR)/ng45_designs/bsg_chip_ng45_0.50.sdc
export SYNTH_NETLIST_FILES = $(TESTCASE_DIR)/ng45_designs/bsg_chip_ng45_0.50.v

export FASTROUTE_TCL = $(PLATFORM_DIR)/fastroute.tcl
export ADDITIONAL_LEFS = $(PLATFORM_DIR)/lef/fakeram45_32x32.lef \
						 $(PLATFORM_DIR)/lef/fakeram45_128x116.lef \
						 $(PLATFORM_DIR)/lef/fakeram45_256x48.lef \
						 $(PLATFORM_DIR)/lef/fakeram45_512x64.lef \
						 $(PLATFORM_DIR)/lef/fakeram45_64x62.lef \
						 $(PLATFORM_DIR)/lef/fakeram45_64x124.lef
	
export ADDITIONAL_LIBS = $(PLATFORM_DIR)/lib/fakeram45_32x32.lib \
						 $(PLATFORM_DIR)/lib/fakeram45_128x116.lib \
						 $(PLATFORM_DIR)/lib/fakeram45_256x48.lib \
						 $(PLATFORM_DIR)/lib/fakeram45_512x64.lib \
						 $(PLATFORM_DIR)/lib/fakeram45_64x62.lib \
						 $(PLATFORM_DIR)/lib/fakeram45_64x124.lib

export PDN_TCL = $(PLATFORM_DIR)/grid_strategy-M1-M4-M7_ng45_pdnfix.tcl

# export CORE_UTILIZATION ?= 65
export CORE_UTILIZATION = 68
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT        = 0
export REMOVE_CELLS_FOR_EQY   = TAPCELL*
export GPL_ROUTABILITY_DRIVEN = 0
export GPL_TIMING_DRIVEN = 0
export MACRO_BLOCKAGE_HALO = 2

# workaround for high congestion in post-grt repair
export SKIP_INCREMENTAL_REPAIR = 1
