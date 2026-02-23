# Extra make rules layered on top of OpenROAD-flow-scripts.
#
# ORFS generates some SDCs as side-effects of OpenROAD steps, but the default
# Makefile includes copy rules that list those SDCs as prerequisites without a
# way for make to discover how to create them. This breaks when running into a
# fresh WORK_HOME (common for cloud runs).

$(RESULTS_DIR)/2_1_floorplan.sdc: $(RESULTS_DIR)/2_1_floorplan.odb ;
$(RESULTS_DIR)/5_1_grt.sdc: $(RESULTS_DIR)/5_1_grt.odb ;
