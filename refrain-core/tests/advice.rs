// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
use std::path::PathBuf;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

fn proto() -> Protocol {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/advisor_ap.ir.json");
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn chunks(ev: &mut Evaluator, n: usize) {
    for _ in 0..n {
        ev.step_chunk_events(&vec![vec![1.0_f64]; 256]);
    }
}

#[test]
fn advice_follows_the_session() {
    let mut ev = Evaluator::new(&proto(), 256.0, &["Cz".to_string()]);
    ev.start(false);
    assert_eq!(ev.advice()["reason"], "not_training_phase");
    chunks(&mut ev, 12);
    let a = ev.advice();
    assert_eq!(a["reason"], "collecting");
    assert_eq!(a["evidence"]["clean_s"], 2.0);
    ev.drain_advice_events();
    ev.set_control("xover", 0.7).unwrap();
    assert_eq!(ev.drain_advice_events()[0]["kind"], "changed_manually");
    ev.mark_equipment_change();
    assert_eq!(ev.advice()["reason"], "equipment_settling");
    assert_eq!(ev.autopilot_policy()["controls"]["xover"]["apply"], "auto");
    assert!(ev.apply_advice("adv-0001", "practitioner").is_err());
}
