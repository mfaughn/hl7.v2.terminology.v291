#!/usr/bin/env python3
"""
Fix canonical URLs and versions in Frank Oemig's V2 terminology IG.

For each CodeSystem and ValueSet:
1. Replace Frank's custom canonical URL with the UTG/THO canonical URL
2. Preserve Frank's version in a "v2-semantic-version" extension
3. Set version to next major version relative to UTG (if changes exist)

THO versioning policy: integer increments in semver format (e.g., 3.0.0 -> 4.0.0)

Usage:
    python3 fix_canonicals_and_versions.py --utg /workspace/utg [--dry-run]
"""

import json
import os
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from collections import defaultdict

FHIR_NS = {'fhir': 'http://hl7.org/fhir'}
V2_SEMANTIC_VERSION_URL = (
    "http://terminology.hl7.org/StructureDefinition/v2-semantic-version"
)


def load_utg_versions(utg_path):
    """Load canonical URLs and versions from UTG V2 CodeSystems and ValueSets.

    Returns dicts keyed by table number:
        cs_info[table_no] = {'url': ..., 'version': ..., 'id': ...}
        vs_info[table_no] = {'url': ..., 'version': ..., 'id': ...}
    """
    cs_info = {}
    vs_info = {}

    cs_dir = os.path.join(utg_path, 'input', 'sourceOfTruth', 'v2', 'codeSystems')
    vs_dir = os.path.join(utg_path, 'input', 'sourceOfTruth', 'v2', 'valueSets')

    for dirname, target in [(cs_dir, cs_info), (vs_dir, vs_info)]:
        if not os.path.isdir(dirname):
            continue
        for fname in os.listdir(dirname):
            if not fname.endswith('.xml'):
                continue
            fpath = os.path.join(dirname, fname)
            try:
                tree = ET.parse(fpath)
                root = tree.getroot()
            except ET.ParseError:
                continue

            rid_el = root.find('fhir:id', FHIR_NS)
            url_el = root.find('fhir:url', FHIR_NS)
            ver_el = root.find('fhir:version', FHIR_NS)

            rid = rid_el.get('value', '') if rid_el is not None else ''
            url = url_el.get('value', '') if url_el is not None else ''
            ver = ver_el.get('value', '') if ver_el is not None else ''

            m = re.search(r'v2-(\d{4})', rid) or re.search(r'v2-(\d{4})', url)
            if not m:
                m = re.search(r'v2-(\d{4})', fname)
            if not m:
                continue

            table_no = m.group(1)
            target[table_no] = {
                'url': url,
                'version': ver,
                'id': rid,
            }

    return cs_info, vs_info


def get_table_number(resource):
    """Extract the V2 table number from a Frank IG resource."""
    # CodeSystems: use codesystem-tableNo extension
    for ext in resource.get('extension', []):
        if 'tableNo' in ext.get('url', ''):
            return ext.get('valueString', '')

    # ValueSets: look for v2-XXXX in identifiers
    for ident in resource.get('identifier', []):
        val = ident.get('value', '')
        m = re.search(r'v2-(\d{4})', val)
        if m:
            return m.group(1)

    return ''


def next_major_version(utg_version):
    """Compute next major version in THO semver format.

    THO policy: increment major version by 1, reset minor and patch.
    e.g., '3.0.0' -> '4.0.0', '5.0.0' -> '6.0.0'
    """
    if not utg_version:
        return '1.0.0'
    parts = utg_version.split('.')
    try:
        major = int(parts[0])
        return f'{major + 1}.0.0'
    except (ValueError, IndexError):
        return '1.0.0'


def has_semantic_version_extension(resource):
    """Check if resource already has the v2-semantic-version extension."""
    for ext in resource.get('extension', []):
        if ext.get('url', '') == V2_SEMANTIC_VERSION_URL:
            return True
    return False


def fix_codesystem(resource, utg_cs_info, comparison_data, dry_run=False):
    """Fix a single CodeSystem resource. Returns (changed, details)."""
    table_no = get_table_number(resource)
    if not table_no:
        return False, "no table number found"

    details = []
    changed = False

    utg = utg_cs_info.get(table_no)
    frank_url = resource.get('url', '')
    frank_version = resource.get('version', '')

    # 1. Fix canonical URL
    if utg:
        new_url = utg['url']
        if frank_url != new_url:
            details.append(f"url: {frank_url} -> {new_url}")
            resource['url'] = new_url
            changed = True
    else:
        # No UTG equivalent — construct the expected canonical
        new_url = f"http://terminology.hl7.org/CodeSystem/v2-{table_no}"
        if frank_url != new_url:
            details.append(f"url: {frank_url} -> {new_url} (no UTG equivalent)")
            resource['url'] = new_url
            changed = True

    # 2. Preserve Frank's version in extension
    if frank_version and not has_semantic_version_extension(resource):
        ext = {
            "url": V2_SEMANTIC_VERSION_URL,
            "valueString": frank_version,
        }
        if 'extension' not in resource:
            resource['extension'] = []
        resource['extension'].append(ext)
        details.append(f"preserved Frank's version {frank_version} in extension")
        changed = True

    # 3. Set new version
    if utg:
        utg_version = utg['version']
        # Check if there are actual differences from comparison data
        table_has_changes = table_no in comparison_data.get('tables_with_changes', set())
        if table_has_changes:
            new_version = next_major_version(utg_version)
            details.append(f"version: {frank_version} -> {new_version} "
                           f"(UTG was {utg_version}, changes detected)")
        else:
            # No changes — match UTG version
            new_version = utg_version
            details.append(f"version: {frank_version} -> {new_version} "
                           f"(matches UTG, no changes)")
    else:
        # New to UTG — start at 1.0.0
        new_version = '1.0.0'
        details.append(f"version: {frank_version} -> {new_version} (new to UTG)")

    resource['version'] = new_version

    # 4. Fix valueSet reference if present (should match UTG VS canonical)
    if 'valueSet' in resource and utg:
        vs_url = f"http://terminology.hl7.org/ValueSet/v2-{table_no}"
        if resource['valueSet'] != vs_url:
            details.append(f"valueSet: {resource['valueSet']} -> {vs_url}")
            resource['valueSet'] = vs_url
            changed = True

    return changed or new_version != frank_version, details


def fix_valueset(resource, utg_vs_info, dry_run=False):
    """Fix a single ValueSet resource. Returns (changed, details)."""
    table_no = get_table_number(resource)
    if not table_no:
        return False, "no table number found"

    details = []
    changed = False

    utg = utg_vs_info.get(table_no)
    frank_url = resource.get('url', '')
    frank_version = resource.get('version', '')

    # 1. Fix canonical URL
    if utg:
        new_url = utg['url']
        if frank_url != new_url:
            details.append(f"url: {frank_url} -> {new_url}")
            resource['url'] = new_url
            changed = True
    else:
        new_url = f"http://terminology.hl7.org/ValueSet/v2-{table_no}"
        if frank_url != new_url:
            details.append(f"url: {frank_url} -> {new_url} (no UTG equivalent)")
            resource['url'] = new_url
            changed = True

    # 2. Preserve Frank's version in extension
    if frank_version and not has_semantic_version_extension(resource):
        ext = {
            "url": V2_SEMANTIC_VERSION_URL,
            "valueString": frank_version,
        }
        if 'extension' not in resource:
            resource['extension'] = []
        resource['extension'].append(ext)
        details.append(f"preserved Frank's version {frank_version} in extension")
        changed = True

    # 3. Set new version (ValueSets follow the same pattern)
    if utg:
        new_version = utg.get('version', '1.0.0') or '1.0.0'
        # For ValueSets we match UTG version for now
        # (version bumps should be driven by CodeSystem changes)
    else:
        new_version = '1.0.0'

    if frank_version != new_version:
        details.append(f"version: {frank_version} -> {new_version}")
        resource['version'] = new_version
        changed = True

    # 4. Fix compose.include system references
    compose = resource.get('compose', {})
    for inc in compose.get('include', []):
        system = inc.get('system', '')
        if 'v2plusvocab' in system:
            new_system = f"http://terminology.hl7.org/CodeSystem/v2-{table_no}"
            details.append(f"compose.include.system: {system} -> {new_system}")
            inc['system'] = new_system
            changed = True

    return changed, details


def load_comparison_data(comparison_path):
    """Load comparison report to determine which tables have changes."""
    data = {'tables_with_changes': set()}
    if not comparison_path or not os.path.exists(comparison_path):
        return data

    with open(comparison_path) as f:
        report = json.load(f)

    for t in report.get('tables', []):
        # A table has changes if it has code differences
        if t.get('codeDifferences'):
            data['tables_with_changes'].add(t['tableNumber'])

    return data


def main():
    parser = argparse.ArgumentParser(
        description='Fix canonical URLs and versions in Frank Oemig V2 terminology IG'
    )
    parser.add_argument('--utg', required=True, help='Path to UTG repo')
    parser.add_argument('--comparison',
                        default='/workspace/v2ig/v291-extracted/vocabulary-comparison-report.json',
                        help='Path to comparison report JSON')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without modifying files')
    args = parser.parse_args()

    print("Loading UTG data...")
    utg_cs, utg_vs = load_utg_versions(args.utg)
    print(f"  {len(utg_cs)} CodeSystems, {len(utg_vs)} ValueSets")

    print("Loading comparison data...")
    comparison = load_comparison_data(args.comparison)
    print(f"  {len(comparison['tables_with_changes'])} tables with changes")

    resources_dir = 'input/resources'
    cs_changed = 0
    cs_total = 0
    vs_changed = 0
    vs_total = 0
    all_details = []

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Processing resources...")

    for fname in sorted(os.listdir(resources_dir)):
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(resources_dir, fname)
        with open(fpath) as f:
            resource = json.load(f)

        rtype = resource.get('resourceType', '')

        if rtype == 'CodeSystem':
            cs_total += 1
            changed, details = fix_codesystem(
                resource, utg_cs, comparison, args.dry_run
            )
            if changed:
                cs_changed += 1
                all_details.append((fname, details))
                if not args.dry_run:
                    with open(fpath, 'w') as f:
                        json.dump(resource, f, indent=2, ensure_ascii=False)
                        f.write('\n')

        elif rtype == 'ValueSet':
            vs_total += 1
            changed, details = fix_valueset(resource, utg_vs, args.dry_run)
            if changed:
                vs_changed += 1
                all_details.append((fname, details))
                if not args.dry_run:
                    with open(fpath, 'w') as f:
                        json.dump(resource, f, indent=2, ensure_ascii=False)
                        f.write('\n')

    # Print summary
    print(f"\n=== Summary ===")
    print(f"CodeSystems: {cs_changed}/{cs_total} modified")
    print(f"ValueSets:   {vs_changed}/{vs_total} modified")

    if all_details:
        print(f"\n=== Details ({len(all_details)} files) ===")
        for fname, details in all_details:
            print(f"\n  {fname}:")
            for d in details:
                print(f"    - {d}")


if __name__ == '__main__':
    main()
