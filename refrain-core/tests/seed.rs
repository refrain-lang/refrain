//! Baseline seeding (Task 14): the Rust seed latch. Mirrors the Python
//! `_SeedLatch` fixture in `tests/test_eval_seed.py` for the run-edge fire.

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

fn load(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

#[test]
fn fires_once_and_writes_the_measured_percentile() {
    let p = load("seed_run");
    let mut ev = Evaluator::new(&p, 256.0, &["Cz".to_string()]);
    ev.start(false);
    for _ in 0..4 {
        ev.step_chunk_events(&vec![vec![5.0_f64]; 256]);
    } // 3 warmup + 1 run chunk
    let r = ev.seed_report();
    let e = &r["thr_uv"];
    assert_eq!(e.status, "seeded");
    assert!((e.value.unwrap() - 5.0).abs() < 1e-9, "seeded value {:?}", e.value);
    assert!((e.target_pct - 70.0).abs() < 1e-9);
}
