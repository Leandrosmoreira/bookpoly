#!/usr/bin/env python3
"""
Generate Polymarket API credentials from private key.

SECURITY: This script uses py-clob-client (official Polymarket library)
to generate API credentials. Your private key is used locally and never
transmitted to third-party servers.
"""
import os
import sys

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
except ImportError:
    print("❌ ERROR: py-clob-client not installed")
    print("Install with: pip install py-clob-client")
    sys.exit(1)


def normalize_private_key(key: str) -> str:
    """Normalize private key (remove 0x prefix if present)."""
    key = key.strip()
    if key.startswith("0x"):
        key = key[2:]
    return key


def main():
    print("=" * 60)
    print(" POLYMARKET API KEY GENERATOR")
    print("=" * 60)
    print()
    print("This script generates API credentials from your private key.")
    print("Your private key is used locally and never sent to third parties.")
    print()

    # Get private key
    print("📋 Step 1: Private Key")
    print("   Get it from: https://reveal.magic.link/polymarket")
    print("   Or from Polymarket app: Cash → Menu → Export Private Key")
    print()
    private_key = input("Enter your private key (with or without 0x): ").strip()
    
    if not private_key:
        print("❌ Private key is required")
        sys.exit(1)

    # Normalize private key
    private_key = normalize_private_key(private_key)
    
    if len(private_key) != 64:
        print(f"❌ Invalid private key length: {len(private_key)} (expected 64 hex characters)")
        sys.exit(1)

    # Get funder address
    print()
    print("📋 Step 2: Funder Address (Wallet)")
    print("   Get it from: https://polymarket.com/settings")
    print()
    funder = input("Enter your Polymarket wallet address (0x...): ").strip()
    
    if not funder:
        print("❌ Funder address is required")
        sys.exit(1)

    if not funder.startswith("0x") or len(funder) != 42:
        print(f"❌ Invalid funder address format (expected 0x followed by 40 hex chars)")
        sys.exit(1)

    print()
    print("🔄 Generating API credentials...")
    print()

    try:
        # Initialize ClobClient with private key
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,  # Polygon
            key=private_key,
            funder=funder,
            signature_type=1
        )

        # Generate API credentials from private key
        print("   Calling API to generate credentials...")
        creds = client.create_or_derive_api_creds()
        
        if not creds:
            print("❌ Failed to generate credentials")
            print("   Make sure your private key and funder address are correct")
            print("   Check your internet connection")
            sys.exit(1)

        # Display credentials
        print("=" * 60)
        print(" ✅ SUCCESS! Add these to your .env file:")
        print("=" * 60)
        print()
        print("# Polymarket API Credentials")
        print(f"POLYMARKET_API_KEY={creds.api_key}")
        print(f"POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
        print(f"POLYMARKET_FUNDER={funder}")
        print(f"POLYMARKET_PRIVATE_KEY=0x{private_key}")
        print(f"POLYMARKET_SIGNATURE_TYPE=1")
        print()
        print("# Enable Claim (optional)")
        print("CLAIM_ENABLED=true")
        print("CLAIM_DRY_RUN=true  # Set to false for live claims")
        print()
        print("=" * 60)

        # Ask to save
        try:
            save = input("\n💾 Save to .env.polymarket? (y/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n\n⚠️  Interrupted. Credentials were generated but not saved.")
            print("   Copy the credentials above manually to your .env file.")
            sys.exit(0)
        
        if save == 'y':
            env_file = "/root/bookpoly/.env.polymarket"
            try:
                with open(env_file, 'w') as f:
                    f.write("# Polymarket API Credentials\n")
                    f.write(f"POLYMARKET_API_KEY={creds.api_key}\n")
                    f.write(f"POLYMARKET_API_SECRET={creds.api_secret}\n")
                    f.write(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}\n")
                    f.write(f"POLYMARKET_FUNDER={funder}\n")
                    f.write(f"POLYMARKET_PRIVATE_KEY=0x{private_key}\n")
                    f.write(f"POLYMARKET_SIGNATURE_TYPE=1\n")
                    f.write("\n# Enable Claim (optional)\n")
                    f.write("CLAIM_ENABLED=true\n")
                    f.write("CLAIM_DRY_RUN=true\n")
                
                print(f"✅ Saved to {env_file}")
                print()
                print("📋 To add to .env, run:")
                print(f"   cat {env_file} >> /root/bookpoly/.env")
                print()
            except Exception as e:
                print(f"❌ Error saving file: {e}")
                print("   Copy the credentials above manually to your .env file.")
        else:
            print("\n📋 Credentials not saved. Copy them manually to your .env file.")

    except Exception as e:
        print(f"❌ Error generating credentials: {e}")
        print()
        print("Common issues:")
        print("  - Invalid private key format")
        print("  - Wrong funder address")
        print("  - Network connection issue")
        sys.exit(1)


if __name__ == "__main__":
    main()

