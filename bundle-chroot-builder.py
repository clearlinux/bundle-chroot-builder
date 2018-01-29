#!/usr/bin/env python3
#
# Copyright © 2015-2016 Intel Corporation.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Software Update server bundle chroot creator"""

# pylint: disable=R0912
# pylint: disable=R0914
# pylint: disable=R0915
# pylint: disable=W0703

import argparse
import configparser
import os
import os.path
import io
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import multiprocessing


def handle_options():
    """Process command arguments"""
    aparser = argparse.ArgumentParser()
    aparser.add_argument("version", help="New build version")
    aparser.add_argument("-m", "--mix-version", help="Mix version for SWUPD mixer")
    aparser.add_argument("-c", "--config", help="Path to config file")
    args = aparser.parse_args()
    return args


def get_config(args):
    buildconf='/usr/share/defaults/bundle-chroot-builder/builder.conf'
    if os.path.isfile('/etc/bundle-chroot-builder/builder.conf'):
        buildconf = '/etc/bundle-chroot-builder/builder.conf'
    if args.config:
        buildconf = args.config

    print("Reading from %s" % buildconf)
    cfg_txt = ""
    # Check that the environment variables in the config file are valid
    pattern = re.compile("\$\{?(\w+)\}?")
    for i, line in enumerate(open(buildconf, 'r')):
        for match in re.finditer(pattern, line):
            if not match.group(1) in os.environ:
                print("ERROR:\nbuilder.conf contains an undefined environment variable: %s on line %s\n"
                        % (i+1, match.group(1)))
                exit(1)
        cfg_txt += os.path.expandvars(line)

    config = configparser.ConfigParser()
    config.readfp(io.StringIO(cfg_txt))
    return config


def read_config(args):
    config = get_config(args)
    for option in ['SERVER_STATE_DIR', 'BUNDLE_DIR', 'YUM_CONF']:
            if config.has_option('Builder', option) == False:
                print("ERROR:\nbuilder.conf is missing:\n[Builder]\n%s\n" % option)
                exit(1)

    """Read the configuration file for our script values"""
    conf = config['Builder']
    state_dir = conf['SERVER_STATE_DIR']
    bundles = conf['BUNDLE_DIR']
    yum_conf = conf['YUM_CONF']

    return state_dir, bundles, yum_conf


def install_bundle(out_dir, postfix, bundle, bundles, yum_cmd):
    """Helper function to yum install a bundle"""
    lines = []
    try:
        output = subprocess.check_output(["m4", bundles + "/" + bundle], cwd=bundles, bufsize=1, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        print('ERROR {0}: m4 failed on {1}/{2}'.format(e.returncode, bundles, bundle))
        raise
    for line in output:
        lines.append(line)

    pkgs = "".join(lines)
    to_install = []
    for pkg in pkgs.splitlines():
        pkg = pkg.strip()
        # Don't add blank lines or lines with leading '#'
        if len(pkg) == 0 or pkg[0] == "#":
            continue
        to_install.append(pkg)
    subprocess.check_output(yum_cmd + ["--installroot={0}/{1}" .format(out_dir, postfix), "install"] + to_install)
    includes = []
    with open(bundles + "/" + bundle, "r") as bfile:
        for line in bfile.readlines():
            if line.startswith("include("):
                # 8 characters skips 'include('
                end_position = line.find(")")
                includes.append(line[8:end_position] + "\n")
    with open("{0}/{1}-includes".format(out_dir, bundle), "w") as ifile:
        ifile.writelines(includes)


def process_bundle(out_dir, bundle, bundles, yum_cmd):
    subprocess.check_output(["cp", "-a", "--preserve=all", "{}/os-core".format(out_dir), "{0}/{1}".format(out_dir, bundle)])
    install_bundle(out_dir, bundle, bundle, bundles, yum_cmd)
    with open(out_dir + "/packages-{}".format(bundle), "w") as file:
        subprocess.Popen(['rpm', '--root={0}/{1}'.format(out_dir, bundle),
                          '-qa', '--queryformat', '%{NAME}\t%{SOURCERPM}\n'], stdout=file).wait()
    with open(out_dir + "/packages-{}".format(bundle), "r") as file:
        raw_list = file.readlines()
    for line in raw_list:
        # Line is 'pkg\tpkg.src.rpm\n'
        subpackage, srpm = line.split("\t")
        # Drop newline
        srpm = srpm[:-1]
        with open(out_dir + "/pkgmap-{0} * {1}".format(bundle, srpm), "a") as file:
            subprocess.Popen(['rpm', '--root={0}/{1}'.format(out_dir, bundle),
                              '-ql', subpackage], stdout=file).wait()
    bundle_prefix = out_dir + "/" + bundle + "/usr/share/clear/bundles/"
    with open(bundle_prefix + bundle, "a"):
        os.utime(bundle_prefix + bundle, None)
    clean_bundle(out_dir, bundle, bundles, yum_cmd)


def clean_bundle(out_dir, bundle, bundles, yum_cmd):
    mode_00755 = (stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    mode_01777 = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO | stat.S_ISVTX
    prev_dir = os.getcwd()
    os.chdir(out_dir + "/" + bundle)
    # must use relative paths here or we kill the host
    shutil.rmtree("./var/lib/")
    os.mkdir("./var/lib/")
    os.chmod("./var/lib/", mode_00755)
    shutil.rmtree("./var/cache/")
    os.mkdir("./var/cache/")
    os.chmod("./var/cache/", mode_00755)
    shutil.rmtree("./var/log/")
    os.mkdir("./var/log/")
    os.chmod("./var/log/", mode_00755)
    shutil.rmtree("./dev/")
    os.mkdir("./dev/")
    os.chmod("./dev/", mode_00755)
    shutil.rmtree("./run/")
    os.mkdir("./run/")
    os.chmod("./run/", mode_00755)
    shutil.rmtree("./tmp/")
    os.mkdir("./tmp/")
    os.chmod("./tmp/", mode_01777)
    os.chdir(prev_dir)


def write_default_server_ini(state_dir, server_conf):
    """
    Write default server.ini file in the state dir in the following format

    [Server]
    emptydir={state_dir}/empty/
    imagebase={state_dir}/image/
    outputdir={state_dir}/www/

    [Debuginfo]
    banned=true
    lib=/usr/lib/debug/
    src=/usr/src/debug/
    """
    contents = ("[Server]\n"
                "emptydir={0}/empty/\n"
                "imagebase={0}/image/\n"
                "outputdir={0}/www/\n".format(state_dir))
    if server_conf:
        contents += ("\n[Debuginfo]\n"
                    "banned={}\n"
                    "lib={}\n"
                    "src={}\n"
                    .format(server_conf.get('debuginfo_banned'),
                            server_conf.get('debuginfo_lib'),
                            server_conf.get('debuginfo_src')))

    with open(state_dir + "/server.ini", "w+") as serverini:
        serverini.write(contents)


def create_chroots(args, state_dir, bundles, yum_conf):
    """The state_dir should always be created if it does not exist"""
    if os.path.isdir(state_dir) == False:
        os.makedirs(state_dir)
    if os.path.isdir(state_dir + "/image") == False:
        os.makedirs(state_dir + "/image")
    if os.path.isdir(state_dir + "/www") == False:
        os.makedirs(state_dir + "/www")
    if os.path.isdir(state_dir + "/image/0") == False:
        os.makedirs(state_dir + "/image/0")
    if os.path.isdir(state_dir + "/www/0") == False:
        os.makedirs(state_dir + "/www/0")

    # Create server.ini and groups.ini for create_update later on
    config = get_config(args)
    config = config['Server'] if 'Server' in config else {}
    write_default_server_ini(state_dir, config)
    with open(state_dir + "/groups.ini", "w+") as groupsini:
        bundle_list = os.listdir(bundles)
        bundle_list = trim_bundles(bundle_list)
        for bundle in bundle_list:
            groupsini.write("[{0}]\ngroup={0}\n\n".format(bundle))

    """Setup chroots for bundles"""
    bversion = ""
    blines = []
    if args.mix_version:
        out_version = args.mix_version
    else:
        out_version = args.version
    build_version = args.version

    if os.path.exists(state_dir + "/image/LAST_VER") == False:
        with open(state_dir + "/image/LAST_VER", "w") as latestver:
            latestver.write("0\n")

    config = configparser.ConfigParser()
    config.read(yum_conf)

    """Read the yum config to find which url to use"""
    if 'local' in config:
        conf_baseurl = config['local']['baseurl']
        print(conf_baseurl)
    else:
        conf_baseurl = config['clear']['baseurl']
        url = conf_baseurl.split("$releasever")[0] + build_version + conf_baseurl.split("$releasever")[1]
        conf_baseurl = url

    out_dir = state_dir + "/image/" + out_version

    if platform.dist()[0] == "fedora" and int(platform.dist()[1]) >= 22:
        print("using dnf instead of yum")
        packager = ["dnf"]
    else:
        packager = ["yum"]
    yum_cmd = packager + ["--config={}".format(yum_conf), "-y", "--releasever={}".format(build_version)]
    if 'local' in config == False:
        try:
            urllib.request.urlopen(conf_baseurl)
        except Exception as excep:
            print("Unable to retrieve {}".format(conf_baseurl))
            print(excep)
            sys.exit(-2)
    if os.path.isdir(out_dir):
        print("Removing pre-existing {} before starting".format(out_dir))
        os.system('rm -rf '+out_dir)
    # if os.path.isdir(out_dir):
    #     print("Removing pre-existing {} before starting".format(out_dir))
    #     shutil.rmtree(out_dir)
    print("Preparing new {}".format(out_dir))
    print("  based on bundles from: {}".format(bundles))
    print("  and yum config: {}".format(yum_conf))

    print("Creating os-core bundle")
    os.makedirs(out_dir + "/os-core/var/lib/rpm")

    print("Initializing rpm database")
    subprocess.check_output(["rpm", "--root", "{}/os-core".format(out_dir), "--initdb"])

    print("Cleaning yum cache")
    subprocess.check_output(yum_cmd + ["--installroot={}/os-core".format(out_dir), "clean", "all"])

    print("Yum installing os-core filesystem")
    subprocess.check_output(yum_cmd + ["--installroot={}/os-core".format(out_dir), "install", "filesystem"])

    print("Yum installing packages from os-core")
    install_bundle(out_dir, "os-core", "os-core", bundles, yum_cmd)

    os.makedirs(out_dir + "/os-core/usr/share/clear", exist_ok=True)
    with open(out_dir + "/os-core/usr/share/clear/version", "w") as file:
        file.writelines([out_version])
    with open(out_dir + "/os-core/usr/share/clear/versionstamp", "w") as file:
        file.writelines([str(int(time.time()))])

    bundle_prefix = out_dir + "/os-core/usr/share/clear/bundles/"
    os.makedirs(bundle_prefix)
    with open(bundle_prefix + "os-core", "a"):
        os.utime(bundle_prefix + "os-core", None)

    print("Noting os-core package list")
    with open(out_dir + "/versions", "w") as file:
        subprocess.Popen(yum_cmd + ["--quiet", "--installroot={}/os-core".format(out_dir), "list"], stdout=file).wait()
    with open(out_dir + "/packages-os-core", "w") as file:
        subprocess.Popen(['rpm', '--root={}/os-core'.format(out_dir),
                          '-qa', '--queryformat', '%{NAME}\t%{SOURCERPM}\n'], stdout=file).wait()
    with open(out_dir + "/os-core/usr/lib/os-release", "r") as file:
        lines = file.readlines()
        for line in lines:
            if line.startswith("VERSION_ID="):
                bversion = line.split("=")[1]
                if bversion != out_version:
                    line = "VERSION_ID={}\n".format(out_version)
            blines.append(line)
    with open(out_dir + "/os-core/usr/lib/os-release", "w") as file:
        file.writelines(blines)

    bundle_list = os.listdir(bundles)
    bundle_list.remove('os-core')
    bundle_list = trim_bundles(bundle_list)
    pool = multiprocessing.Pool()
    results_list = []

    def progress(r):
        print('.', end="", flush=True)

    for bundle in bundle_list:
        print("Scheduling to process bundle {}".format(bundle))
        r = pool.apply_async(process_bundle, (out_dir, bundle, bundles, yum_cmd), callback=progress)
        results_list.append(r)
    print("Waiting for tasks to complete...")
    pool.close()
    pool.join()
    print(' done!')
    print("Retrieving results...")
    for r in results_list:
        r.get()

    """Read the URL values from builder.conf and insert them into os-core-update to swupd knows where to pull content from"""
    config = get_config(args)

    """Read the configuration file for our script values"""
    for option in ['BUNDLE', 'CONTENTURL', 'VERSIONURL', 'FORMAT']:
        if config.has_option('swupd', option) == False:
            print("ERROR:\nbuilder.conf is missing:\n[swupd]\n%s\n" % option)
            exit(1)
    conf = config['swupd']
    bundlename = conf['BUNDLE']
    contenturl = conf['CONTENTURL']
    versionurl = conf['VERSIONURL']
    formatname = conf['FORMAT']

    print("Adding SWUPD default values to '{}' bundle...".format(bundlename))
    """Do not add a leading slash on anything except the first variable in os.path.join!"""
    confpath = os.path.join(out_dir, bundlename, "usr/share/defaults/swupd/")
    os.makedirs(confpath, exist_ok=True)
    with open(os.path.join(confpath, "contenturl"), "w") as file:
        file.writelines(contenturl)
    print("  contenturl: {}".format(contenturl))
    with open(os.path.join(confpath, "versionurl"), "w") as file:
        file.writelines(versionurl)
    print("  versionurl: {}".format(versionurl))
    with open(os.path.join(confpath, "format"), "w") as file:
        file.writelines(formatname)
    print("  format: {}".format(formatname))

    print("Creating package to file mappings")
    package_mapping = {}
    map_files = [f for f in os.listdir(out_dir) if f.startswith("pkgmap-")]
    for map_file in map_files:
        # map_file's name is "pkgmap-{bundle} * {pkg}.src.rpm"
        # The " * " is pure magic but unlikely enough to not show up in a
        # real package's filename I'm using it instead of recursing through
        # temporary per bundle folders
        package_name = map_file[map_file.find(" * ") + 3:-len(".src.rpm")]
        with open(out_dir + "/" +  map_file, "rb") as file:
            path_list = file.readlines()
        if package_name not in package_mapping:
            package_mapping[package_name] = set()
        for path in path_list:
            # RPM prints out a specific string for subpackages that contain no
            # files. It should be excluded from the SRPM file list.
            if re.match(br"\(contains no files\)\n", path):
                continue
            package_mapping[package_name].add(path)
    for package_name, paths in package_mapping.items():
        with open(out_dir + "/files-{}".format(package_name), "wb") as file:
            file.writelines(sorted(paths))
    for map_file in map_files:
        os.unlink(out_dir + "/" + map_file)

    print("Cleaning os-core...")
    clean_bundle(out_dir, 'os-core', bundles, yum_cmd)

    print("Cleaning package list")
    web_dir = state_dir + "/www/" + out_version + "/"
    image_dir = state_dir + "/image/" + out_version + "/"
    if os.path.isdir(web_dir):
        print("  removing pre-existing {} before setting version file" .format(web_dir))
        os.system('rm -rf '+web_dir)
    # FIXME: Figure out problems with the below to replace the above.
    # Consider all uses of os.system("rm -fr") as FIXMEs of the same variety.
    # if os.path.isdir(image_dir):
    #     print("  removing pre-existing {} before setting version file"
    #           .format(imagedir())
    #     shutil.rmtree(image_dir)
    os.makedirs(image_dir + '/noship')
    versions_output = []
    with open(out_dir + "/versions", "r") as file:
        versions = set()
        lines = file.readlines()
        for line in lines:
            if line.startswith("Available") or \
               line.startswith("Installed") or \
               line.startswith("BDB2053") or \
               line.startswith("rpm") or line.startswith("yum"):
                continue
            name, pver = re.search("^([^\t ]*)[\t ]*([^\t ]*)",
                                   line).groups()
            versions.add(name + ":" + pver)
        versions = sorted(versions)
        versions_output.append("{0: <50}{1}\n".format("Available", "Packages"))
        for name, pver in [line.split(":") for line in versions]:
            versions_output.append("{0: <50}{1}\n".format(name, pver))
    with open(image_dir + "versions", "w") as file:
        file.writelines(versions_output)
    bundle_list = os.listdir(bundles)
    bundle_list = trim_bundles(bundle_list)
    for bundle in bundle_list:
        shutil.copyfile(out_dir + "/packages-{}".format(bundle), image_dir + "/noship/packages-{}".format(bundle))
        if os.path.isfile(out_dir + "/{}-includes".format(bundle)):
            shutil.copyfile(out_dir + "/{}-includes".format(bundle), image_dir + "/noship/{}-includes".format(bundle))
    for package_name in package_mapping.keys():
        shutil.copyfile(out_dir + "/files-{}".format(package_name),
                        image_dir + "/noship/files-{}".format(package_name))

# Remove bundles with blacklisted characters, such as dot files
def trim_bundles(bundles):
    for bundle in bundles:
        if bundle.startswith('.'):
            bundles.remove(bundle)
    return bundles

def main():
    """Entry point for chroot creator"""
    args = handle_options()
    state_dir, bundles, yum_conf = read_config(args)
    create_chroots(args, state_dir, bundles, yum_conf)

if __name__ == '__main__':
    main()
    sys.exit(0)
