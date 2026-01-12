export DESIGN_NICKNAME = ariane
export DESIGN_NAME = ariane
export PLATFORM    = nangate45

TESTCASE_DIR := $(abspath $(dir $(DESIGN_CONFIG)))
export SDC_FILE = $(TESTCASE_DIR)/ng45_designs/ariane.sdc
export SYNTH_NETLIST_FILES = $(TESTCASE_DIR)/ng45_designs/ariane.v

export FASTROUTE_TCL = $(PLATFORM_DIR)/fastroute.tcl
export ADDITIONAL_LEFS = $(PLATFORM_DIR)/lef/fakeram45_256x16.lef
export ADDITIONAL_LIBS = $(PLATFORM_DIR)/lib/fakeram45_256x16.lib

export PDN_TCL = $(PLATFORM_DIR)/grid_strategy-M1-M4-M7_ng45_pdnfix.tcl

export CORE_UTILIZATION = 68
export PLACE_DENSITY_LB_ADDON = 0.60
export TNS_END_PERCENT        = 0
export REMOVE_CELLS_FOR_EQY   = TAPCELL*
export GPL_ROUTABILITY_DRIVEN = 0
export GPL_TIMING_DRIVEN = 0
export MACRO_PLACE_HALO = 5 5
export MACRO_BLOCKAGE_HALO = 5

# workaround for high congestion in post-grt repair
export SKIP_INCREMENTAL_REPAIR = 1
