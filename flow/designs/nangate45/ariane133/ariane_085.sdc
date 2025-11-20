set sdc_version 2.0

# Set the current design
current_design ariane

create_clock -name "core_clock" -period 3.4 -waveform {0.0 2.0} [get_ports clk_i]
