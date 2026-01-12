export DESIGN_NICKNAME = ariane
export DESIGN_NAME = ariane
export PLATFORM    = nangate45

TESTCASE_DIR := $(abspath $(dir $(DESIGN_CONFIG)))
export SDC_FILE = $(TESTCASE_DIR)/ng45_designs/ariane.sdc
export SYNTH_NETLIST_FILES = $(TESTCASE_DIR)/ng45_designs/ariane.v

# Timing closure priority: setup (ECP). Allow hold to violate.
export REPAIR_TIMING_SETUP_ONLY = 1
export SETUP_REPAIR_SEQUENCE = unbuffer,sizedown,sizeup,swap,buffer,clone,split

export FASTROUTE_TCL = $(PLATFORM_DIR)/fastroute_ng45_ecp.tcl
export ADDITIONAL_LEFS = $(PLATFORM_DIR)/lef/fakeram45_256x16.lef
export ADDITIONAL_LIBS = $(PLATFORM_DIR)/lib/fakeram45_256x16.lib

export PDN_TCL = $(PLATFORM_DIR)/grid_strategy-M1-M4-M7_ng45_pdnfix.tcl

override export DONT_USE_CELLS = TAPCELL_X1 FILLCELL_X1
export CORE_UTILIZATION = 68
export PLACE_DENSITY_LB_ADDON = 0.60
export TNS_END_PERCENT        = 100
export MAX_REPAIRS_PER_PASS   = 5
export MAX_REPAIR_TIMING_ITER = 50
export FLOORPLAN_TNS_END_PERCENT = 0
export FLOORPLAN_MAX_REPAIRS_PER_PASS = 1
export FLOORPLAN_MAX_REPAIR_TIMING_ITER = 1
export REMOVE_CELLS_FOR_EQY   = TAPCELL*
export GPL_ROUTABILITY_DRIVEN = 0
export GPL_TIMING_DRIVEN = 1
# Only keep timing-driven resizer changes once placement overflow is low.
export GPL_KEEP_OVERFLOW = 0.6
export MACRO_PLACE_HALO = 5 5
export MACRO_BLOCKAGE_HALO = 1
export ENABLE_PLACE_REPAIR_TIMING = 1

# Unified timing-driven placement knobs (OpenROAD GPL/RSZ extensions).
export GLOBAL_PLACEMENT_ARGS = \
  -timing_driven_net_reweight_overflow {90 80 70 60 50 40 30 20 10} \
  -timing_driven_nets_percentage 60 \
  -td_enable_dynamic_weights \
  -td_ramp_iterations 20 \
  -td_update_period 5 \
  -td_weight_max 20.0 \
  -td_severity_weight_limit 50.0 \
  -td_congestion_alpha 0.0 \
  -td_initial_nets_percent 10.0 \
  -td_final_nets_percent 60.0 \
  -td_slack_norm 5e-11 \
  -td_severity_slack_norm 2e-10 \
  -td_overflow_limit 0.4 \
  -td_top_endpoints 2000 \
  -td_congestion_gate 10.0 \
  -td_hot_bin_fraction 1.0 \
  -td_hot_bin_threshold 0.20 \
  -cws_enable \
  -cws_top_endpoints 1000 \
  -cws_min_path_length 8 \
  -aas_enable \
  -aas_top_paths 200 \
  -aas_min_path_length 8

# workaround for high congestion in post-grt repair
export SKIP_INCREMENTAL_REPAIR = 0
