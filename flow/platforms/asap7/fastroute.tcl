# Configure global routing adjustment. Prefer explicit two-level per-layer
# adjustments (PIN_LAYER_ADJUST/ABOVE_LAYER_ADJUST) when provided, otherwise
# fall back to the platform default.
if { [info exists ::env(PIN_LAYER_ADJUST)] && $::env(PIN_LAYER_ADJUST) != "" \
  && [info exists ::env(ABOVE_LAYER_ADJUST)] && $::env(ABOVE_LAYER_ADJUST) != "" } {
  set min_layer $::env(MIN_ROUTING_LAYER)
  set max_layer $::env(MAX_ROUTING_LAYER)

  set next1 ""
  set next2 ""
  if { [regexp {^([A-Za-z_]+)([0-9]+)$} $min_layer -> prefix num] } {
    set next1 "${prefix}[expr {$num + 1}]"
    set next2 "${prefix}[expr {$num + 2}]"
  }

  if { $next1 != "" && $next2 != "" } {
    set_global_routing_layer_adjustment $min_layer-$next1 $::env(PIN_LAYER_ADJUST)
    set_global_routing_layer_adjustment $next2-$max_layer $::env(ABOVE_LAYER_ADJUST)
  } else {
    set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.25
  }
} else {
  set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.25
}
set_routing_layers -clock $::env(MIN_CLK_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER)
set_routing_layers -signal $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER)
