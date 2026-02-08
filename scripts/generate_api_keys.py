#!/usr/bin/env python3
"""
Generate Polymarket API Keys from Magic Email Account

How to use:
1. Export your private key from https://reveal.magic.link/polymarket
   Or: Polymarket -> Cash -> Menu (3 dots) -> "Export Private Key"

2. Get your funder address from https://polymarket.com/settings
   (The wallet address shown is your proxy wallet/funder)

3. Run this script:
   python scripts/generate_api_keys.py

4. Copy the output to your .env file
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("=" * 60)
    print("ERROR: py-clob-client not installed")
    print("=" * 60)
    print("\nRun: pip install py-clob-client")
    print()
    sys.exit(1)


def main():
    print("=" * 60)
    print(" Polymarket API Key Generator (Magic Email)")
    print("=" * 60)
    print()

    # Get private key
    print("Step 1: Export your private key from:")
    print("  https://reveal.magic.link/polymarket")
    print("  Or: Polymarket -> Cash -> Menu -> 'Export Private Key'")
    print()

    private_key = input("Enter your private key (with or without 0x): ").strip()

    if not private_key:
        print("ERROR: Private key is required")
        sys.exit(1)

    # Remove 0x prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    # Validate key length (should be 64 hex characters)
    if len(private_key) != 64:
        print(f"WARNING: Private key length is {len(private_key)}, expected 64")
        print("Make sure you copied the full key")

    print()

    # Get funder address
    print("Step 2: Get your wallet address from:")
    print("  https://polymarket.com/settings")
    print()

    funder = input("Enter your Polymarket wallet address (0x...): ").strip()

    if not funder:
        print("ERROR: Funder address is required")
        sys.exit(1)

    if not funder.startswith("0x"):
        funder = "0x" + funder

    print()
    print("Generating API credentials...")
    print()

    try:
        # Create client with Magic/Email signature type
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,  # Polygon mainnet
            signature_type=1,  # 1 = POLY_PROXY (Magic/Email)
            funder=funder
        )

        # Generate or derive API credentials
        try:
            creds = client.derive_api_key()
            print("Derived existing API credentials")
        except:
            creds = client.create_api_key()
            print("Created new API credentials")

        print()
        print("=" * 60)
        print(" SUCCESS! Add these to your .env file:")
        print("=" * 60)
        print()
        print(f"# Polymarket API Credentials (Magic Email)")
        print(f"POLYMARKET_API_KEY={creds.api_key}")
        print(f"POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
        print(f"POLYMARKET_FUNDER={funder}")
        print(f"POLYMARKET_PRIVATE_KEY={private_key}")
        print(f"POLYMARKET_SIGNATURE_TYPE=1")
        print()
        print("# Enable Claim Sweeper")
        print("CLAIM_ENABLED=true")
        print("CLAIM_DRY_RUN=true  # Set to false for live claims")
        print()
        print("=" * 60)
        print()

        # Optionally save to file
        save = input("Save to .env.polymarket? (y/n): ").strip().lower()
        if save == 'y':
            env_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ".env.polymarket"
            )
            with open(env_path, "w") as f:
                f.write("# Polymarket API Credentials (Magic Email)\n")
                f.write(f"POLYMARKET_API_KEY={creds.api_key}\n")
                f.write(f"POLYMARKET_API_SECRET={creds.api_secret}\n")
                f.write(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}\n")
                f.write(f"POLYMARKET_FUNDER={funder}\n")
                f.write(f"POLYMARKET_PRIVATE_KEY={private_key}\n")
                f.write(f"POLYMARKET_SIGNATURE_TYPE=1\n")
                f.write("\n")
                f.write("# Claim Sweeper\n")
                f.write("CLAIM_ENABLED=true\n")
                f.write("CLAIM_DRY_RUN=true\n")
            print(f"Saved to: {env_path}")
            print()
            print("Now copy the contents to your main .env file:")
            print(f"  cat {env_path} >> .env")

    except Exception as e:
        print(f"ERROR: {e}")
        print()
        print("Common issues:")
        print("  - Invalid private key format")
        print("  - Wrong funder address")
        print("  - Network issues connecting to Polymarket")
        sys.exit(1)


if __name__ == "__main__":
    main()
