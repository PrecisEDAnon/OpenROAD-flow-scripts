include designs/sky130hd/jpeg/config.mk

# Stress knobs to create long-tail DRT behavior:
# - allow global-route congestion to reach DRT
# - increase congestion iterations/reporting to amplify hotspot difficulty
export PLACE_DENSITY_LB_ADDON = 0.25
export GLOBAL_ROUTE_ARGS = -congestion_iterations 30 -congestion_report_iter_step 5 -verbose -allow_congestion

