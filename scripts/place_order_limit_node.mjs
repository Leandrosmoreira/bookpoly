#!/usr/bin/env node
/**
 * Envia ordem limit POST-ONLY via cliente TypeScript @polymarket/clob-client.
 * Use este script se o Python der "invalid signature" (type 1).
 *
 * Uso: node place_order_limit_node.mjs
 * Env: carregado de ../.env (POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER, etc.)
 */

import { config } from "dotenv";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { Wallet } from "ethers";
import { ClobClient, OrderType, Side } from "@polymarket/clob-client";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: resolve(__dirname, "..", ".env") });

const HOST = process.env.CLOB_BASE_URL || "https://clob.polymarket.com";
const CHAIN_ID = 137;
const GAMMA = process.env.GAMMA_BASE_URL || "https://gamma-api.polymarket.com";

async function getBtc15minToken() {
  const windowTs = Math.floor(Date.now() / 1000 / 900) * 900;
  const slug = `btc-updown-15m-${windowTs}`;
  const r = await fetch(`${GAMMA}/events/slug/${slug}`);
  if (!r.ok) return [null, null];
  const event = await r.json();
  const markets = event.markets || [];
  if (!markets.length) return [null, null];
  let tokens = markets[0].clobTokenIds;
  if (typeof tokens === "string") tokens = JSON.parse(tokens);
  if (!tokens || tokens.length < 2) return [null, null];
  return [tokens[0], event.title || slug];
}

async function main() {
  const key = process.env.POLYMARKET_PRIVATE_KEY;
  const funder = process.env.POLYMARKET_FUNDER;
  if (!key || !funder) {
    console.error("Defina POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env");
    process.exit(1);
  }

  const wallet = new Wallet(key.startsWith("0x") ? key : "0x" + key);
  const [tokenId, title] = await getBtc15minToken();
  if (!tokenId) {
    console.error("Mercado BTC 15min nao encontrado");
    process.exit(1);
  }

  console.log("=== ORDEM LIMIT POST-ONLY (Node/TS client) ===");
  console.log("Mercado:", title);
  console.log("Token YES:", tokenId.slice(0, 30) + "...");

  const client = new ClobClient(
    HOST,
    CHAIN_ID,
    wallet,
    undefined,
    1, // signatureType = 1 (Poly Proxy / Magic)
    funder
  );

  const creds = await client.createOrDeriveApiKey();
  client.creds = creds;

  const price = 0.01;
  const size = 5;
  console.log("\nOrdem: BUY", size, "@", price, "(POST ONLY)");
  console.log("Custo max: $" + (price * size).toFixed(2));

  const resp = await client.createAndPostOrder(
    { tokenID: tokenId, price, size, side: Side.BUY },
    undefined,
    OrderType.GTC,
    false,
    true // postOnly
  );

  console.log("\n=== ORDEM ENVIADA ===");
  console.log(JSON.stringify(resp, null, 2));
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
