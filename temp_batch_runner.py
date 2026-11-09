import subprocess
import sys
import time

TOTAL_BATCHES = 1
COUNT = 15

def main():
    print("=" * 50)
    print(f"  Starting {TOTAL_BATCHES} batches of {COUNT} problems")
    print("=" * 50)

    for i in range(1, TOTAL_BATCHES + 1):
        print(f"\n{'=' * 50}")
        print(f"  BATCH {i} / {TOTAL_BATCHES}")
        print(f"{'=' * 50}\n")

        cmd = [
            "uv", "run", "main.py",
            "--solve",
            "--status", "yet to start",
            "--difficulty", "easy",
            "--count", str(COUNT),
            "--max-tries", "1"
        ]

        try:
            # Run and stream output live to console
            result = subprocess.run(cmd, check=False)
            
            if result.returncode != 0:
                print(f"\n⚠️  Batch {i} exited with code {result.returncode}")
        except KeyboardInterrupt:
            print("\n\n🛑 Interrupted by user. Exiting.")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ Batch {i} crashed: {e}")

        if i < TOTAL_BATCHES:
            print(f"\n⏳ Sleeping 5s before batch {i + 1}...")
            time.sleep(5)

    print("\n" + "=" * 50)
    print("  ALL BATCHES COMPLETE")
    print("=" * 50)
    
    # Final stats
    subprocess.run(["uv", "run", "main.py", "--stats"])


if __name__ == "__main__":
    main()
    