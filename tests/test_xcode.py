from __future__ import annotations

from zmb_xcode_data import parse_xcode_overview


def main() -> None:
    sample = '''protocol=1
developer_dir=/Applications/Xcode.app/Contents/Developer
xcode_version=Xcode 26.1
xcode_build=Build version 17B55
simulator_app=1
simctl_ok=1
---SIMCTL-JSON---
{"runtimes":[{"identifier":"com.apple.CoreSimulator.SimRuntime.iOS-26-1","name":"iOS 26.1"}],"devices":{"com.apple.CoreSimulator.SimRuntime.iOS-26-1":[{"name":"iPhone 17 Pro","udid":"01234567-89AB-CDEF-0123-456789ABCDEF","state":"Booted","isAvailable":true},{"name":"Unavailable","udid":"11111111-2222-3333-4444-555555555555","state":"Shutdown","isAvailable":false}]}}
'''
    meta, devices = parse_xcode_overview(sample)
    assert meta['xcode_version'] == 'Xcode 26.1'
    assert len(devices) == 1
    assert devices[0]['name'] == 'iPhone 17 Pro'
    assert devices[0]['runtime'] == 'iOS 26.1'
    assert devices[0]['state'] == 'Booted'

    meta, devices = parse_xcode_overview('simctl_ok=0\nsimctl_error=not ready\n---SIMCTL-JSON---\n{}\n')
    assert meta['simctl_ok'] == '0' and devices == []

    meta, devices = parse_xcode_overview('simctl_ok=1\n---SIMCTL-JSON---\nnot-json\n')
    assert 'simctl_error' in meta and devices == []
    print('xcode parser tests: OK')


if __name__ == '__main__':
    main()
