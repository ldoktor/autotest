"""
Install server interfaces, for autotest client machine OS provisioning.
"""
import os, xmlrpclib, logging, time
from autotest.client.shared import error


def remove_hosts_file():
    """
    Remove the ssh known hosts file for a machine.

    Sometimes it is useful to have this done since on a test lab, SSH
    fingerprints of the test machines keep changing all the time due
    to frequent reinstalls.
    """
    known_hosts_file = "%s/.ssh/known_hosts" % os.getenv("HOME")
    if os.path.isfile(known_hosts_file):
        logging.debug("Deleting known hosts file %s", known_hosts_file)
        os.remove(known_hosts_file)


class CobblerInterface(object):
    """
    Implements interfacing with the Cobbler install server.

    @see: https://fedorahosted.org/cobbler/
    """
    def __init__(self, **kwargs):
        """
        Sets class attributes from the keyword arguments passed to constructor.

        @param **kwargs: Dict of keyword arguments passed to constructor.
        """
        self.xmlrpc_url = kwargs['xmlrpc_url']
        self.user = kwargs['user']
        self.password = kwargs['password']
        self.fallback_profile = kwargs['fallback_profile']
        if self.xmlrpc_url:
            self.server = xmlrpclib.Server(self.xmlrpc_url)
            self.token = self.server.login(self.user, self.password)


    def get_system_handle(self, host):
        """
        Get a system handle, needed to perform operations on the given host

        @param host: Host name

        @return: Tuple (system, system_handle)
        """
        try:
            system = self.server.find_system({"name" : host.hostname})[0]
        except IndexError, detail:
            ### TODO: Method to register this system as brand new
            logging.error("Error finding %s: %s", host.hostname, detail)
            raise ValueError("No system %s registered on install server" %
                             host.hostname)

        system_handle = self.server.get_system_handle(system, self.token)
        return (system, system_handle)


    def install_host(self, host, profile=None, timeout=None):
        """
        Install a host object with profile name defined by distro.

        @param host: Autotest host object.
        @param profile: String with cobbler profile name.
        @param timeout: Amount of time to wait for the install.
        """
        if self.xmlrpc_url:

            step_time = 60
            if timeout is None:
                # 1 hour of timeout by default
                timeout = 1 * 3600

            logging.info("Setting up machine %s install", host.hostname)
            remove_hosts_file()

            system, system_handle = self.get_system_handle(host)

            if profile is None:
                profile = self.fallback_profile

            system_info = self.server.get_system(system)
            current_profile = system_info.get('profile')
            # If no fallback profile is enabled, we don't want to mess
            # with the currently profile set for that machine.
            if profile and (profile != current_profile):
                self.server.modify_system(system_handle, 'profile', profile,
                                          self.token)
            else:
                profile = current_profile

            # Enable netboot for that machine (next time it'll reboot and be
            # reinstalled)
            self.server.modify_system(system_handle, 'netboot_enabled', 'True',
                                      self.token)
            try:
                # Cobbler only generates the DHCP configuration for netboot enabled
                # machines, so we need to synchronize the dhcpd file after changing
                # the value above
                self.server.sync_dhcp(self.token)
            except xmlrpclib.Fault, err:
                # older Cobbler will not recognize the above command
                if not "unknown remote method" in err.faultString:
                    logging.error("DHCP sync failed, error code: %s, error string: %s",
                                  err.faultCode, err.faultString)
            # Now, let's just restart the machine (machine has to have
            # power management data properly set up).
            self.server.save_system(system_handle, self.token)
            self.server.power_system(system_handle, 'reboot', self.token)
            host.record("START", None, "install", host.hostname)
            host.record("GOOD", None, "install.start", host.hostname)
            logging.info("Installing machine %s with profile %s (timeout %s s)",
                         host.hostname, profile, timeout)
            install_start = time.time()
            time_elapsed = 0
            install_successful = False
            while time_elapsed < timeout:
                time.sleep(step_time)
                system_info = self.server.get_system(system)
                install_successful = not system_info.get('netboot_enabled')
                if install_successful:
                    break
                time_elapsed = time.time() - install_start

            if not install_successful:
                e_msg = 'Host %s install timed out' % host.hostname
                host.record("END FAIL", None, "install", e_msg)
                raise error.HostInstallTimeoutError(e_msg)

            host.wait_for_restart()
            host.record("END GOOD", None, "install", host.hostname)
            time_elapsed = time.time() - install_start
            logging.info("Machine %s installed successfuly after %d s (%d min)",
                         host.hostname, time_elapsed, time_elapsed/60)


    def power_host(self, host, state='reboot'):
        """
        Power on/off/reboot a host through cobbler.

        @param host: Autotest host object.
        @param state: Allowed states - one of 'on', 'off', 'reboot'.
        """
        if self.xmlrpc_url:
            system_handle = self.get_system_handle(host)[1]
            self.server.power_system(system_handle, state, self.token)
