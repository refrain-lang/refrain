// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
//! The Rust advisor must reproduce the Python reference exactly (values
//! compared as numbers, so `1` == `1.0`). Fixtures: tools/gen_fixtures.py.

use std::path::PathBuf;

use refrain_core::advisor::{trace_protocol, trace_to_json, Advisor, OwnedFacts};
use refrain_core::ir::Protocol;
use serde_json::Value;

fn fixture(name: &str) -> Value {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name);
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn same(got: &Value, want: &Value, path: &str) {
    match (got, want) {
        (Value::Number(a), Value::Number(b)) => {
            assert_eq!(a.as_f64(), b.as_f64(), "{path}: rust {a} vs python {b}")
        }
        (Value::Object(a), Value::Object(b)) => {
            let ka: Vec<_> = a.keys().collect();
            let kb: Vec<_> = b.keys().collect();
            assert_eq!(ka, kb, "{path}: keys differ");
            for (k, v) in a {
                same(v, &b[k], &format!("{path}.{k}"));
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}: length rust {} vs python {}", a.len(), b.len());
            for (i, (x, y)) in a.iter().zip(b).enumerate() {
                same(x, y, &format!("{path}[{i}]"));
            }
        }
        _ => assert_eq!(got, want, "{path}"),
    }
}

#[test]
fn tracer_matches_python() {
    let want = fixture("advisor_trace.json");
    for (stem, expected) in want.as_object().unwrap() {
        let p: Protocol = serde_json::from_value(fixture(stem)).unwrap();
        same(&trace_to_json(&trace_protocol(&p)), expected, stem);
    }
}

#[test]
fn scenarios_match_python() {
    let all = fixture("advisor_scenarios.json");
    for (name, sc) in all.as_object().unwrap() {
        let p: Protocol = serde_json::from_value(sc["ir"].clone()).unwrap();
        let mut adv = Advisor::new(&p, sc["sample_rate_hz"].as_f64().unwrap());
        for (i, step) in sc["steps"].as_array().unwrap().iter().enumerate() {
            let op = &step["op"];
            let at = format!("{name}#{i}({})", op["op"]);
            let mut result = Value::Null;
            let mut error = false;
            match op["op"].as_str().unwrap() {
                "feed" => {
                    let f = OwnedFacts::from_json(op);
                    adv.feed(&f.view());
                }
                "apply" => match adv.apply(op["id"].as_str().unwrap(), op["by"].as_str().unwrap()) {
                    Ok((_, _, ev)) => result = ev,
                    Err(_) => error = true,
                },
                "dismiss" => match adv.dismiss(op["id"].as_str().unwrap()) {
                    Ok(ev) => result = ev,
                    Err(_) => error = true,
                },
                "note" => match adv.note_control(op["control"].as_str().unwrap(),
                                                 op["value"].as_f64().unwrap(),
                                                 op["source"].as_str().unwrap()) {
                    Ok(()) => {}
                    Err(_) => error = true,
                },
                "equipment" => adv.mark_equipment_change(),
                other => panic!("unknown op {other}"),
            }
            assert_eq!(error, step["error"].as_bool().unwrap(), "{at}: error flag");
            same(&result, &step["result"], &format!("{at}.result"));
            same(&adv.advice(), &step["advice"], &format!("{at}.advice"));
            same(&Value::Array(adv.drain_events()), &step["events"], &format!("{at}.events"));
        }
    }
}
