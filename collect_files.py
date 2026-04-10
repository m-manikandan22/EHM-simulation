import os

ROOT = r"C:\Users\ELCOT\Music\TNWISE\simulation"
OUTPUT_FILE = os.path.join(ROOT, "all_files.txt")  # unified name

# Extensions to include
INCLUDE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".css", ".html", ".json", ".txt",
    ".cfg", ".ini", ".env",
    ".yaml", ".yml", ".toml",
    ".sh", ".bat", ".vue"
    # Add ".md" if needed
}

# Directories to skip
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules",
    "dist", ".claude", ".github"
}

# Files to skip
SKIP_FILES = {
    "all_file.txt", "all_files.txt",
    "all_files.md", "collect_files.py",
    "package-lock.json"
}

def collect_files():
    collected = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Remove unwanted dirs
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()

            if filename in SKIP_FILES:
                continue

            if ext not in INCLUDE_EXTS:
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, ROOT)

            collected.append((rel_path, full_path))

    return sorted(collected, key=lambda x: x[0])


def write_output(files):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        for rel_path, full_path in files:
            out.write("\n" + "=" * 80 + "\n")
            out.write(f"FILE: {rel_path}\n")
            out.write("=" * 80 + "\n")

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        out.write(f"{line_num:>4}: {line.rstrip()}\n")
            except Exception as e:
                out.write(f"[ERROR reading file: {e}]\n")


if __name__ == "__main__":
    files = collect_files()
    write_output(files)

    print(f"\n✅ Done! Total files collected: {len(files)}")
    for rel, _ in files:
        print(f"  - {rel}")