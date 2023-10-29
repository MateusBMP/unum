if __name__ == '__main__':
    import sys  
    from . import unum_cli

    rc = 1

    try:
        unum_cli.main()
        rc = 0
    except Exception as e:
        print('Error: %s' % e, file=sys.stderr)

    sys.exit(rc)
