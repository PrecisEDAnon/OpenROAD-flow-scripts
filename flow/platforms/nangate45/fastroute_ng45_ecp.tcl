set_global_routing_layer_adjustment metal2-metal3 0.5
# ng45 pre-synth testcases can be extremely dense; relax metal4+ derating to
# avoid small residual global-route overflow without disabling congestion checks.
set_global_routing_layer_adjustment metal4-$::env(MAX_ROUTING_LAYER) 0.06

set_routing_layers -clock $::env(MIN_CLK_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER)
set_routing_layers -signal $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER)
