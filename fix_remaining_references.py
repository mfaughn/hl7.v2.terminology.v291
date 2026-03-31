#!/usr/bin/env python3
"""
Fix ALL remaining v2plusvocab references throughout Frank's IG resources.

Handles:
- identifier[].value
- CodeSystem.valueSet
- ValueSet.compose.include.system
- extension[].url (codesystem-tableNo, versionHistory, etc.)
- property[].uri
- text.div (narrative HTML)

Also produces a detailed JSON changelog and a human-readable markdown summary.

Usage:
    python3 fix_remaining_references.py [--dry-run]
"""

import json
import os
import re
import argparse
from collections import defaultdict


# Mapping of Frank's extension URLs to THO-compatible equivalents.
# These are structural extensions Frank defined under his v2plusvocab namespace.
# We remap them to terminology.hl7.org without the v2plusvocab segment.
EXTENSION_URL_MAP = {
    "http://terminology.hl7.org/v2plusvocab/StructureDefinition/codesystem-tableNo":
        "http://terminology.hl7.org/StructureDefinition/v2-codesystem-tableNo",
    "http://terminology.hl7.org/v2plusvocab/StructureDefinition/codesystem-v2versionCreated":
        "http://terminology.hl7.org/StructureDefinition/v2-codesystem-versionCreated",
    "http://terminology.hl7.org/v2plusvocab/StructureDefinition/codesystem-versionHistory":
        "http://terminology.hl7.org/StructureDefinition/v2-codesystem-versionHistory",
}

# Mapping of Frank's property URIs
PROPERTY_URI_PREFIX = "http://terminology.hl7.org/v2plusvocab/CodeSystem/Property#"
PROPERTY_URI_NEW_PREFIX = "http://terminology.hl7.org/CodeSystem/v2-property#"


def get_table_number(resource):
    """Extract V2 table number from a resource."""
    for ext in resource.get('extension', []):
        if 'tableNo' in ext.get('url', ''):
            return ext.get('valueString', '')
    for ident in resource.get('identifier', []):
        m = re.search(r'v2-(\d{4})', ident.get('value', ''))
        if m:
            return m.group(1)
    return ''


def fix_all_references(resource, fname):
    """Fix every v2plusvocab reference in a resource. Returns list of changes."""
    changes = []
    rtype = resource.get('resourceType', '')
    table_no = get_table_number(resource)

    # Handle structural resources (StructureDefinitions, Property CodeSystem)
    # that don't have a table number but still need v2plusvocab stripped.
    if not table_no:
        raw_before = json.dumps(resource)
        if 'v2plusvocab' not in raw_before:
            return changes
        # Generic fix: replace v2plusvocab in url field
        url = resource.get('url', '')
        if 'v2plusvocab' in url:
            new_url = url.replace('/v2plusvocab/', '/')
            changes.append({
                'field': 'url',
                'old': url,
                'new': new_url,
            })
            resource['url'] = new_url
        # Replace throughout the entire resource as a string
        raw_after = json.dumps(resource).replace(
            'terminology.hl7.org/v2plusvocab/',
            'terminology.hl7.org/'
        )
        if raw_after != json.dumps(resource):
            updated = json.loads(raw_after)
            resource.clear()
            resource.update(updated)
            changes.append({
                'field': '(global string replacement)',
                'old': 'v2plusvocab references in structural resource',
                'new': 'v2plusvocab segment removed',
            })
        return changes

    canonical_cs = f"http://terminology.hl7.org/CodeSystem/v2-{table_no}"
    canonical_vs = f"http://terminology.hl7.org/ValueSet/v2-{table_no}"

    # 1. Fix identifier values
    for ident in resource.get('identifier', []):
        val = ident.get('value', '')
        if 'v2plusvocab' in val:
            new_val = canonical_cs if rtype == 'CodeSystem' else canonical_vs
            changes.append({
                'field': 'identifier.value',
                'old': val,
                'new': new_val,
            })
            ident['value'] = new_val

    # 2. Fix CodeSystem.valueSet
    if rtype == 'CodeSystem' and 'valueSet' in resource:
        if 'v2plusvocab' in resource.get('valueSet', ''):
            changes.append({
                'field': 'valueSet',
                'old': resource['valueSet'],
                'new': canonical_vs,
            })
            resource['valueSet'] = canonical_vs

    # 3. Fix ValueSet.compose.include.system
    if rtype == 'ValueSet':
        for inc in resource.get('compose', {}).get('include', []):
            if 'v2plusvocab' in inc.get('system', ''):
                changes.append({
                    'field': 'compose.include.system',
                    'old': inc['system'],
                    'new': canonical_cs,
                })
                inc['system'] = canonical_cs

    # 4. Fix extension URLs
    def fix_extensions(ext_list, path='extension'):
        for ext in ext_list:
            url = ext.get('url', '')
            if url in EXTENSION_URL_MAP:
                new_url = EXTENSION_URL_MAP[url]
                changes.append({
                    'field': f'{path}.url',
                    'old': url,
                    'new': new_url,
                })
                ext['url'] = new_url
            elif 'v2plusvocab' in url:
                # Generic v2plusvocab extension — strip the v2plusvocab segment
                new_url = url.replace('/v2plusvocab/', '/')
                changes.append({
                    'field': f'{path}.url',
                    'old': url,
                    'new': new_url,
                })
                ext['url'] = new_url
            # Recurse into nested extensions
            if 'extension' in ext:
                fix_extensions(ext['extension'], f'{path}.extension')

    fix_extensions(resource.get('extension', []))

    # 5. Fix property URIs
    for prop in resource.get('property', []):
        uri = prop.get('uri', '')
        if uri.startswith(PROPERTY_URI_PREFIX):
            prop_name = uri[len(PROPERTY_URI_PREFIX):]
            new_uri = f"{PROPERTY_URI_NEW_PREFIX}{prop_name}"
            changes.append({
                'field': 'property.uri',
                'old': uri,
                'new': new_uri,
            })
            prop['uri'] = new_uri
        elif 'v2plusvocab' in uri:
            new_uri = uri.replace('/v2plusvocab/', '/')
            changes.append({
                'field': 'property.uri',
                'old': uri,
                'new': new_uri,
            })
            prop['uri'] = new_uri

    # 6. Fix text.div narrative (contains embedded old URLs)
    text = resource.get('text', {})
    div = text.get('div', '')
    if 'v2plusvocab' in div:
        new_div = div.replace('v2plusvocab/', '')
        if new_div != div:
            changes.append({
                'field': 'text.div',
                'old': '(narrative containing v2plusvocab references)',
                'new': '(updated to remove v2plusvocab)',
            })
            text['div'] = new_div

    # 7. Final sweep: catch any remaining v2plusvocab in the entire resource
    raw = json.dumps(resource)
    if 'v2plusvocab' in raw:
        cleaned = raw.replace(
            'terminology.hl7.org/v2plusvocab/',
            'terminology.hl7.org/'
        )
        if cleaned != raw:
            updated = json.loads(cleaned)
            resource.clear()
            resource.update(updated)
            changes.append({
                'field': '(deep sweep)',
                'old': 'remaining v2plusvocab references',
                'new': 'v2plusvocab segment removed',
            })

    return changes


def write_changelog(all_changes, path):
    """Write a detailed JSON changelog."""
    with open(path, 'w') as f:
        json.dump(all_changes, f, indent=2)


def write_changelog_md(all_changes, summary_stats, path):
    """Write a human-readable markdown changelog."""
    lines = []
    lines.append("# Changelog: v2plusvocab Reference Fixes")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Files modified | {summary_stats['files']} |")
    lines.append(f"| Total changes | {summary_stats['total']} |")
    for field, count in sorted(summary_stats['by_field'].items(),
                                key=lambda x: -x[1]):
        lines.append(f"| {field} | {count} |")
    lines.append("")

    lines.append("## Changes by Field Type")
    lines.append("")
    for field, count in sorted(summary_stats['by_field'].items(),
                                key=lambda x: -x[1]):
        lines.append(f"### {field} ({count} changes)")
        lines.append("")
        # Show unique old->new mappings
        mappings = defaultdict(int)
        for fname, file_changes in all_changes.items():
            for c in file_changes:
                if c['field'] == field:
                    key = f"`{c['old']}` → `{c['new']}`"
                    mappings[key] += 1
        if len(mappings) <= 20:
            for mapping, cnt in sorted(mappings.items(), key=lambda x: -x[1]):
                lines.append(f"- {mapping} ({cnt} files)")
        else:
            lines.append(f"- {len(mappings)} unique transformations across "
                         f"{count} instances")
            # Show top 10
            for mapping, cnt in sorted(mappings.items(),
                                        key=lambda x: -x[1])[:10]:
                lines.append(f"  - {mapping} ({cnt} files)")
            lines.append(f"  - ... and {len(mappings)-10} more")
        lines.append("")

    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    resources_dir = 'input/resources'
    all_changes = {}
    summary_stats = {'files': 0, 'total': 0, 'by_field': defaultdict(int)}

    for fname in sorted(os.listdir(resources_dir)):
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(resources_dir, fname)
        with open(fpath) as f:
            resource = json.load(f)

        changes = fix_all_references(resource, fname)
        if changes:
            all_changes[fname] = changes
            summary_stats['files'] += 1
            summary_stats['total'] += len(changes)
            for c in changes:
                summary_stats['by_field'][c['field']] += 1
            if not args.dry_run:
                with open(fpath, 'w') as f:
                    json.dump(resource, f, indent=2, ensure_ascii=False)
                    f.write('\n')

    mode = 'DRY RUN - ' if args.dry_run else ''
    print(f"{mode}Fixed {summary_stats['total']} references "
          f"in {summary_stats['files']} files")
    print(f"\nBy field:")
    for field, count in sorted(summary_stats['by_field'].items(),
                                key=lambda x: -x[1]):
        print(f"  {field}: {count}")

    # Write changelogs
    write_changelog(all_changes, 'CHANGELOG-v2plusvocab-fixes.json')
    write_changelog_md(all_changes, summary_stats,
                       'CHANGELOG-v2plusvocab-fixes.md')
    print(f"\nChangelogs written:")
    print(f"  CHANGELOG-v2plusvocab-fixes.json")
    print(f"  CHANGELOG-v2plusvocab-fixes.md")

    # Verify
    remaining = 0
    remaining_files = []
    for fname in sorted(os.listdir(resources_dir)):
        if not fname.endswith('.json'):
            continue
        with open(os.path.join(resources_dir, fname)) as f:
            if 'v2plusvocab' in f.read():
                remaining += 1
                remaining_files.append(fname)
    print(f"\nFiles still containing v2plusvocab: {remaining}")
    if remaining_files and remaining <= 10:
        for f in remaining_files:
            print(f"  {f}")


if __name__ == '__main__':
    main()
