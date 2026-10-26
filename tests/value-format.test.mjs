import test from "node:test";
import assert from "node:assert/strict";
import {formatted} from "../specledger/static/value-format.mjs";

test("small values and high precision are not rounded to six decimal places", () => {
  assert.equal(formatted(1e-7, "kg", null, "0.0000001"), "0.0000001 kg");
  assert.equal(formatted(1, "kg", null, "1.000000000000000001"), "1.000000000000000001 kg");
});

test("electrical signal attributes remain visible and missing values are explicit", () => {
  assert.equal(formatted(24, "V", "AC", "24"), "24 V AC");
  assert.equal(formatted(24, "V", "DC", "24"), "24 V DC");
  assert.equal(formatted(null, "kg"), "待复核");
  assert.equal(formatted("IP65", null), "IP65");
});
