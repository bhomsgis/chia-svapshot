// Property file with only proven assertions enabled
// Failing assertions are commented out
// To enable failing assertions, uncomment them manually

module tlb_prop

// Automatically detected package imports
import mmu_pkg::*;

#(
		parameter ASSERT_INPUTS = 0
)(
		input logic clk_i,                          // System clock signal.
		input logic rstn_i,                         // System reset signal (active low).
		
		// TLB request-response
		input cache_tlb_comm_t cache_tlb_comm_i,    // Communication from translation requester to TLB.
		input  tlb_cache_comm_t tlb_cache_comm_o,   // Communication from TLB to translation requester. //output
		
		// PTW request-response
		input ptw_tlb_comm_t ptw_tlb_comm_i,        // Communication from TLB to PTW.
		input  tlb_ptw_comm_t tlb_ptw_comm_o,       // Communication from to PTW to TLB. //output
		
		// PMU counter events
		input  logic pmu_tlb_access_o,              // Accepted Translation request to TLB.  //output
		input  logic pmu_tlb_miss_o                 // Accepted Translation request to TLB misses.  //output
	);

//==============================================================================
// Local Parameters
//==============================================================================

genvar j;
default clocking cb @(posedge clk_i);
endclocking
default disable iff (!rstn_i);

// Re-defined wires 

// Symbolics and Handshake signals

//==============================================================================
// Modeling
//==============================================================================


//====DESIGNER-ADDED-SVA====//
a_hit_never_triggers_ptw_req: assert property ((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss |-> !tlb_ptw_comm_o.req.valid))
else begin
    $display("A TLB hit incorrectly triggered a PTW request");
end

a_ptw_req_data_matches_miss_req: assert property ((tlb_ptw_comm_o.req.valid && $past(tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && tlb_cache_comm_o.resp.miss) |-> (tlb_ptw_comm_o.req.vpn == $past(cache_tlb_comm_i.req.vpn[VPN_MAX_SIZE-1:0]) && tlb_ptw_comm_o.req.asid == $past(cache_tlb_comm_i.req.asid) && tlb_ptw_comm_o.req.vmid == $past(cache_tlb_comm_i.req.vmid) && tlb_ptw_comm_o.req.store == $past(cache_tlb_comm_i.req.store) && tlb_ptw_comm_o.req.fetch == $past(cache_tlb_comm_i.req.instruction) && tlb_ptw_comm_o.req.prv == cache_tlb_comm_i.priv_lvl && tlb_ptw_comm_o.req.en_vs == cache_tlb_comm_i.vs_enable && tlb_ptw_comm_o.req.en_g == cache_tlb_comm_i.g_enable)))
else begin
    $display("PTW request data does not match the original cache request that missed");
end

a_ptw_req_is_single_pulse_if_ready: assert property ((tlb_ptw_comm_o.req.valid && ptw_tlb_comm_i.ptw_ready |=> !tlb_ptw_comm_o.req.valid))
else begin
    $display("PTW request was not de-asserted after being accepted by the PTW");
end

a_pmu_access_o_correct: assert property ((pmu_tlb_access_o == (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid)))
else begin
    $display("PMU TLB access event is not correctly tracking accepted requests");
end

a_pmu_miss_o_correct: assert property ((pmu_tlb_miss_o == (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && tlb_cache_comm_o.resp.miss)))
else begin
    $display("PMU TLB miss event is not correctly tracking missed requests");
end

a_passthrough_no_ptw_req: assert property ((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.passthrough |-> !tlb_ptw_comm_o.req.valid))
else begin
    $display("A passthrough request incorrectly triggered a PTW request");
end

a_invalidate_cancels_pending_ptw_req: assert property ((tlb_ptw_comm_o.req.valid && !ptw_tlb_comm_i.ptw_ready && (ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp) |=> !tlb_ptw_comm_o.req.valid))
else begin
    $display("A pending PTW request was not cancelled by an invalidate signal");
end

a_tlb_not_ready_on_miss: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && tlb_cache_comm_o.resp.miss) |=> !tlb_cache_comm_o.tlb_ready))
else begin
    $display("TLB did not become busy (not ready) on the cycle after a miss.");
end

a_tlb_ready_after_cancelled_walk: assert property (((tlb_ptw_comm_o.req.valid && !ptw_tlb_comm_i.ptw_ready && (ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> tlb_cache_comm_o.tlb_ready))
else begin
    $display("TLB did not become ready on the next cycle after a pending page walk was cancelled by an invalidate.");
end

a_ptw_req_deasserted_after_invalidation_when_ready: assert property ((tlb_ptw_comm_o.req.valid && ptw_tlb_comm_i.ptw_ready && (ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp) |=> !tlb_ptw_comm_o.req.valid))
else begin
    $display("PTW request was not cancelled after being accepted in the same cycle as an invalidate.");
end

a_no_new_ptw_req_while_waiting: assert property ((!tlb_cache_comm_o.tlb_ready && !tlb_ptw_comm_o.req.valid && !ptw_tlb_comm_i.resp.valid |=> !tlb_ptw_comm_o.req.valid))
else begin
    $display("A new PTW request was sent while waiting for a previous PTW response.");
end

a_tlb_ready_implies_no_ptw_req: assert property ((tlb_cache_comm_o.tlb_ready |-> !tlb_ptw_comm_o.req.valid))
else begin
    $display("TLB is ready for cache requests but is also sending a request to the PTW");
end

a_ptw_req_implies_not_ready: assert property ((tlb_ptw_comm_o.req.valid |-> !tlb_cache_comm_o.tlb_ready))
else begin
    $display("TLB is sending a request to the PTW but is also ready for cache requests");
end

// a_cache_req_stable_when_not_ready: assert property ((cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.tlb_ready |=> $stable(cache_tlb_comm_i.req) && cache_tlb_comm_i.req.valid))
// else begin
//     $display("Request to TLB changed or deasserted while TLB was not ready");
// end


// a_ptw_req_stable_when_not_ready: assert property ((tlb_ptw_comm_o.req.valid && !ptw_tlb_comm_i.ptw_ready |=> $stable(tlb_ptw_comm_o.req) && tlb_ptw_comm_o.req.valid))
// else begin
//     $display("Request to PTW changed or deasserted while PTW was not ready");
// end

a_miss_triggers_ptw_req: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && tlb_cache_comm_o.resp.miss && !cache_tlb_comm_i.req.passthrough) |=> tlb_ptw_comm_o.req.valid))
else begin
    $display("A non-passthrough TLB miss did not trigger a PTW request on the next cycle");
end

// a_ptw_resp_completes_walk: assert property ((ptw_tlb_comm_i.resp.valid |=> ##1 !tlb_ptw_comm_o.req.valid))
// else begin
//     $display("PTW request was not deasserted after receiving a PTW response");
// end


// a_tlb_ready_after_ptw_resp: assert property ((ptw_tlb_comm_i.resp.valid |=> ##1 tlb_cache_comm_o.tlb_ready))
// else begin
//     $display("TLB did not become ready for cache requests after a PTW response");
// end


// a_exception_implies_hit: assert property (((tlb_cache_comm_o.resp.xcpt.load || tlb_cache_comm_o.resp.xcpt.store || tlb_cache_comm_o.resp.xcpt.fetch) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("An exception was reported on a TLB miss");
// end


// a_guest_exception_implies_hit: assert property (((tlb_cache_comm_o.resp.guest_xcpt.load || tlb_cache_comm_o.resp.guest_xcpt.store || tlb_cache_comm_o.resp.guest_xcpt.fetch) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A guest exception was reported on a TLB miss");
// end


// a_passthrough_is_not_miss: assert property ((cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.passthrough |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A passthrough request was incorrectly flagged as a miss");
// end


// a_invalidate_tlb_flushes_all: assert property ((ptw_tlb_comm_i.invalidate_tlb |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A global TLB invalidation did not cause a subsequent request to miss");
// end


// a_invalidate_satp_flushes_non_virtual: assert property ((ptw_tlb_comm_i.invalidate_tlb_satp |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !cache_tlb_comm_i.vs_enable && !cache_tlb_comm_i.g_enable) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("An SATP-based TLB invalidation did not cause a subsequent non-virtual request to miss");
// end


// a_invalidate_vsatp_flushes_vs_stage: assert property ((ptw_tlb_comm_i.invalidate_tlb_vsatp |=> s_eventually tlb_cache_comm_o.tlb_ready))
// else begin
//     $display("TLB did not become ready after a VSATP invalidate command was issued.");
// end


// a_passthrough_ppn_matches_vpn: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.passthrough) |-> tlb_cache_comm_o.resp.ppn == cache_tlb_comm_i.req.vpn[PPN_SIZE-1:0]))
// else begin
//     $display("For a passthrough request, the output PPN should match the input VPN.");
// end


// a_passthrough_no_exception: assert property ((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.passthrough) |-> (tlb_cache_comm_o.resp.xcpt == '0 && tlb_cache_comm_o.resp.guest_xcpt == '0))
// else begin
//     $display("A passthrough request should not generate an exception.");
// end


// a_tlb_stays_not_ready_during_walk: assert property ((!tlb_cache_comm_o.tlb_ready && !ptw_tlb_comm_i.resp.valid |=> !tlb_cache_comm_o.tlb_ready))
// else begin
//     $display("TLB became ready during a page walk before a PTW response was received.");
// end


// a_ptw_resp_only_when_busy: assert property ((ptw_tlb_comm_i.resp.valid |-> $past(!tlb_cache_comm_o.tlb_ready)))
// else begin
//     $display("PTW response received unexpectedly while TLB was ready for new requests.");
// end


// a_resp_stable_when_not_ready: assert property ((!tlb_cache_comm_o.tlb_ready && !ptw_tlb_comm_i.invalidate_tlb && !ptw_tlb_comm_i.invalidate_tlb_satp && !ptw_tlb_comm_i.invalidate_tlb_vsatp && !ptw_tlb_comm_i.invalidate_tlb_hgatp |=> $stable(tlb_cache_comm_o.resp)))
// else begin
//     $display("TLB response to cache changed while TLB was busy and no invalidate was active.");
// end


// a_invalidate_is_onehot: assert property (($onehot0({ptw_tlb_comm_i.invalidate_tlb, ptw_tlb_comm_i.invalidate_tlb_satp, ptw_tlb_comm_i.invalidate_tlb_vsatp, ptw_tlb_comm_i.invalidate_tlb_hgatp})))
// else begin
//     $display("Multiple TLB invalidate commands were asserted simultaneously.");
// end


// a_hit_is_repeatable: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> (cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i.req)) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A request that was a hit became a miss on the next cycle for an identical request without an external invalidate.");
// end


// a_cache_if_stable_when_not_ready: assert property ((cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.tlb_ready |=> $stable(cache_tlb_comm_i)))
// else begin
//     $display("The cache-to-TLB interface changed while the TLB was not ready to accept a new request.");
// end


// a_miss_is_stable_during_walk: assert property ((!tlb_cache_comm_o.tlb_ready && !ptw_tlb_comm_i.resp.valid |=> $stable(tlb_cache_comm_o.resp.miss)))
// else begin
//     $display("The TLB miss signal changed while a page walk was in progress.");
// end


// a_passthrough_does_not_stall: assert property ((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.passthrough |=> tlb_cache_comm_o.tlb_ready))
// else begin
//     $display("A passthrough request incorrectly caused the TLB to become busy.");
// end


// a_invalidate_hgatp_flushes_g_stage: assert property ((ptw_tlb_comm_i.invalidate_tlb_hgatp |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.g_enable) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("An HGATP-based TLB invalidation did not cause a subsequent G-stage request to miss.");
// end


// a_invalidate_vsatp_flushes_vs_stage_strong: assert property ((ptw_tlb_comm_i.invalidate_tlb_vsatp |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.vs_enable) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A VSATP-based TLB invalidation did not cause a subsequent VS-stage request to miss.");
// end


// a_ptw_error_response_not_cached: assert property ((ptw_tlb_comm_i.resp.valid && ptw_tlb_comm_i.resp.error |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i)) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A PTW error response resulted in a cached entry, causing a subsequent identical request to hit.");
// end


// a_store_to_clean_page_causes_miss: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss && !cache_tlb_comm_i.req.store && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i.req) && cache_tlb_comm_i.req.store) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A store to a page that was previously hit by a non-store request did not cause a miss to set the dirty bit.");
// end


// a_successful_walk_caches_entry: assert property (((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A successful page walk did not result in a TLB hit for an immediate identical request.");
// end


// a_ptw_response_to_invalidated_walk_is_ignored: assert property ((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && $past(!tlb_cache_comm_o.tlb_ready && !tlb_ptw_comm_o.req.valid && (ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i)) |-> tlb_cache_comm_o.resp.miss))
// else begin
//     $display("PTW response for an invalidated page walk was incorrectly cached, resulting in a hit.");
// end


// a_hit_ppn_kilo_page_correct: assert property ((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && ptw_tlb_comm_i.resp.level == KILO_PAGE) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> (tlb_cache_comm_o.resp.ppn == $past(ptw_tlb_comm_i.resp.pte.ppn)))
// else begin
//     $display("PPN for a 4KB page hit does not match the PPN from the preceding PTW response");
// end


// a_hit_ppn_mega_page_correct: assert property ((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && ptw_tlb_comm_i.resp.level == MEGA_PAGE) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> (tlb_cache_comm_o.resp.ppn == {$past(ptw_tlb_comm_i.resp.pte.ppn) >> PAGE_LVL_BITS, cache_tlb_comm_i.req.vpn[PAGE_LVL_BITS-1:0]}))
// else begin
//     $display("PPN for a mega page hit was not constructed correctly from the PTW response and request VPN");
// end


// a_hit_ppn_giga_page_correct: assert property ((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && ptw_tlb_comm_i.resp.level == GIGA_PAGE) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> (tlb_cache_comm_o.resp.ppn == {$past(ptw_tlb_comm_i.resp.pte.ppn) >> (2*PAGE_LVL_BITS), cache_tlb_comm_i.req.vpn[(2*PAGE_LVL_BITS)-1:0]}))
// else begin
//     $display("PPN for a giga page hit was not constructed correctly from the PTW response and request VPN");
// end


// a_access_fault_on_clear_access_bit: assert property ((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && !ptw_tlb_comm_i.resp.pte.a && ptw_tlb_comm_i.resp.pte.v) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> ((cache_tlb_comm_i.req.store && tlb_cache_comm_o.resp.xcpt.store) || (!cache_tlb_comm_i.req.store && !cache_tlb_comm_i.req.instruction && tlb_cache_comm_o.resp.xcpt.load) || (cache_tlb_comm_i.req.instruction && tlb_cache_comm_o.resp.xcpt.fetch)))
// else begin
//     $display("An access to a page with a cleared Access bit did not generate the appropriate page fault exception");
// end


// a_load_fault_on_no_read_page: assert property (((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && ptw_tlb_comm_i.resp.pte.u && !ptw_tlb_comm_i.resp.pte.r && ptw_tlb_comm_i.resp.pte.v && !ptw_tlb_comm_i.ptw_mstatus.mxr) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i) && !cache_tlb_comm_i.req.store && !cache_tlb_comm_i.req.instruction && cache_tlb_comm_i.priv_lvl == '0 && !tlb_cache_comm_o.resp.miss) |-> tlb_cache_comm_o.resp.xcpt.load))
// else begin
//     $display("A load from a non-readable user page did not generate a load page fault exception");
// end


// a_invalidate_satp_spares_virtual_entries: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss && (cache_tlb_comm_i.vs_enable || cache_tlb_comm_i.g_enable) && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_vsatp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> (cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i.req)) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A hit on a virtual entry became a miss after a SATP invalidation, which should only affect non-virtual entries");
// end


// a_invalidate_vsatp_spares_non_vs_entries: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss && !cache_tlb_comm_i.vs_enable && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_hgatp)) |=> (cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i.req)) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A hit on a non-VS entry became a miss after a VSATP invalidation, which should only affect VS-stage entries");
// end


// a_invalidate_hgatp_spares_non_g_entries: assert property (((tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && !tlb_cache_comm_o.resp.miss && !cache_tlb_comm_i.g_enable && !(ptw_tlb_comm_i.invalidate_tlb || ptw_tlb_comm_i.invalidate_tlb_satp || ptw_tlb_comm_i.invalidate_tlb_vsatp)) |=> (cache_tlb_comm_i.req.valid && $stable(cache_tlb_comm_i.req)) |-> !tlb_cache_comm_o.resp.miss))
// else begin
//     $display("A hit on a non-G entry became a miss after an HGATP invalidation, which should only affect G-stage entries");
// end


// a_store_fault_on_no_write_page: assert property (((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && !ptw_tlb_comm_i.resp.pte.w && ptw_tlb_comm_i.resp.pte.v) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.store && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> tlb_cache_comm_o.resp.xcpt.store))
// else begin
//     $display("A store to a non-writable page that hit in the TLB did not generate a store page fault exception.");
// end


// a_fetch_fault_on_no_exec_page: assert property (((ptw_tlb_comm_i.resp.valid && !ptw_tlb_comm_i.resp.error && !ptw_tlb_comm_i.resp.pte.x && ptw_tlb_comm_i.resp.pte.v) |=> ##1 (tlb_cache_comm_o.tlb_ready && cache_tlb_comm_i.req.valid && cache_tlb_comm_i.req.instruction && $stable(cache_tlb_comm_i) && !tlb_cache_comm_o.resp.miss) |-> tlb_cache_comm_o.resp.xcpt.fetch))
// else begin
//     $display("An instruction fetch from a non-executable page that hit in the TLB did not generate a fetch page fault exception.");
// end

endmodule
