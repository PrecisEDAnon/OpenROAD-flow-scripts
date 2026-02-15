# DFT v1.0 spec (`execute_dft_plan`)

## Status (ORFS/OpenROAD clean DFT branches)

As of 2026-02-15, the v1.0 “mandatory” items in this doc are implemented in:
- OpenROAD: `OpenROAD-clean-DFT` @ `dd50bacf29`
- ORFS: `ORFS-clean-DFT` (pins `tools/OpenROAD` to `dd50bacf29`; DFT snapshot `17759df95`)

Known gaps (explicitly called out as “Future extensions” below):
- Multi-bit MBFF / multi-bit ScanFF support.
- Power-domain crossings are warn-only (no automatic level shifter / isolation insertion).
- No SCANDEF import; external “import” is via explicit ordering/constraints inputs.

The command execute_dft_plan should create one or more stitched (i.e., ordered) scan chains, satisfying user-specified constraints.
Each scan chain is a “directed Hamiltonian path” over ScanFF instances. The chain will connect from a legal starting scan-in port of a ScanFF (the first ScanFF in the chain), to a legal ending scan-out port of another ScanFF (the last ScanFF in the chain).
Generally, the one or more scan chains produced by execute_dft_plan attempt to minimize total estimated wirelength (subject to other constraints: hold timing, setup timing criticality, congestion and blockage avoidance, specified ScanFF grouping and ordering, etc.). The estimated wirelength of a given ScanFF1-to-ScanFF2 connection in a scan chain is the Manhattan distance between the scan-out port of ScanFF1 and the scan-in port of ScanFF2.  
Scan chain naming
The user must be able to specify particular scan chain names.
Scan chain grouping, assignment and ordering
The user must be able to assign particular groups of FFs (thus, their respective mapped ScanFF instances) to specific scan chains, or to other groups. (The latter implies that the grouping can be hierarchical.) // if ordering constraints are given without naming the (one) scan chain, will the tool accept this?
The assignment of ScanFFs to a group may include ordering constraints.
Strict order: the scan chain must, for the ScanFFs in a given group, follow the order in which the ScanFFs appear in the assignment to the group. No other ScanFFs may be interpolated within this “sub-path” of the scan chain that is produced. Note that strict order defines a sub-path of the containing “Hamiltonian path”, and note that such a sub-path may make the induced “asymmetric TSP” highly asymmetric: connecting to the scan-in (from the scan-out) port of the sub-path can have very different cost than if the order of ScanFFs in the group were to be reversed.
Partial order: a “before” semantics must be made available to the user, to force a given FF_1 or FF_group_1 to occur in the scan chain solution before another given FF_2 or FF_group_2.
As mentioned in (a) above: a group may be assigned to another group. 
In the execute_dft_plan solution, the ScanFFs assigned to any given group must remain together in a single (i.e., exactly one) scan chain.
Scan chain begin-end port constraints
Each scan chain must have user-specified BeginPort and EndPort locations. These are (x,y) locations (not FFs) – or, pins of placed instances / pads – in the place-and-route region.
The scan chain ordering optimization must include in its calculation of chain cost the estimated wirelength from BeginPort to the first ScanFF’s scan-in port, and the estimated wirelength from the last ScanFF’s scan-out port to EndPort.
Overall scan chain structural constraints
The cost of performing scan-based testing is comprehended by the product architecture. In particular, use of additional on-chip resources, and/or more expensive ATE equipment, can permit the use of multiple scan chains in the solution produced by execute_dft_plan.
The user can specify a maximum length (number of ScanFFs in the chain). This is a constraint on all scan chains in the solution.
The user can specify the number of scan chains in the solution.  This is also a constraint on the overall solution.
The scan chain optimization should comprehend both feasibility and balance.
For example, if max_length * max_num_chains < num_ScanFFs, then no solution will satisfy the constraints and an error should be thrown.
As another example, if the number of ScanFFs in a specified scan group is larger than max_length, then no solution is possible.
Length balancing over all scan chains produced reflects the goal of reducing time spent on the (ATE) tester. A max_imbalance parameter with default of 30 (percent) should be made available to the user.  This adds a simple constraint: the ratio of the lengths of any two scan chains should never exceed (1 + max_imbalance / 100), e.g., a ratio of 1.3 with the default value of 30.
Clock mixing  
Clock mixing refers to stitching ScanFFs from multiple clock domains into a single chain.
See clock_mixing in set_dft_config.
If enabled, then a lockup latch must be instantiated between ScanFFs that are adjacent in a scan chain but belong to different clock domains.
If not enabled, then execute_dft_plan must not output a scan chain stitching solution that mixes ScanFFs from multiple clock domains.
Polarity  
The polarity constraint in its simplest form is that rising edge-triggered and falling edge-triggered ScanFFs cannot coexist in the same scan chain.  
A “mid” way to handle ScanFF polarity is to ensure that all falling edge-triggered ScanFFs exist before all rising edge-triggered ScanFFs in any given scan chain. Note that such a structural constraint may be inconsistent with other constraints induced by grouping and ordering; such an inconsistency should be flagged by the tool.
Polarity is a hard constraint. The tool must comprehend the polarity of all ScanFFs that it stitches together.
KGF inputs 1/29: [KGF] Some other suggestions to consider
You need to be able to exclude certain FFs from scan chain (i.e. reset synchronizer)
In some cases using Qbar output (or changing the FF to one with a Qbar output) can help with setup timing
Shift register recognition, shift registers do not need an additional scan chain through them
Special cell recognition, clock gate cells need to be active during scan and internal tristate drivers should probably be disabled during scan or should not be disturbed by scan.
Stitching to existing scan chains (i.e. in SRAM macros)

The above are mandatory for a v1.0 version. Future extensions would include the following.

Support of multi-bit MBFFs and multi-bit ScanFFs 
Internal scan MBFFs, derived cells, etc. (Google document)
Support of multiple power domains
The tool should “freely” stitch ScanFFs from multiple power domains only if the power domains have the same supply voltage and are always-on.
If ScanFFs from different voltage domains (at different voltage levels) are adjacent in a scan chain, then a level shifter must be inserted.
If either domain is switched, then an isolation cell is needed.
Initially, the tool should throw warnings (but not attempt to implement a correct solution) if (b) or (c) hold.
Support for importing and exporting scan chains to support external tools
This can help future scan insertion tools like Difetto
The tool should be able to take in a list of scan cells 
Sometimes, a scan cell is composed of multiple cells e.g, Fault generates an FF and mux pair if the PDK does not contain scan cells
These cells should be connected together in anywhere from 1 to N initial chains, with a primary input/output per chain. 
Cells may also be connected hierarchically in groups (see 3.) 

…
