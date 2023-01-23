#!/usr/bin/env python

import numpy as np

import carb
from pegasus_isaac.logic.vehicles.vehicle import Vehicle

# Mavlink interface
from pegasus_isaac.mavlink_interface import MavlinkInterface

# Sensors and dynamics setup
from pegasus_isaac.logic.sensors import Barometer, IMU, Magnetometer, GPS
from pegasus_isaac.logic.dynamics import LinearDrag

class Quadrotor(Vehicle):

    def __init__(
        self, 
        # Simulation specific configurations
        stage_prefix: str="quadrotor",  
        usd_file: str="",
        world=None,
        # Spawning pose of the vehicle
        init_pos=[0.0, 0.0, 0.07], 
        init_orientation=[0.0, 0.0, 0.0, 1.0],
        # Rotation direction for the rotors
        rot_dir=[-1,-1,1,1],
        rolling_moment_coefficient=1E-6
    ):

        # Create a mavlink interface for getting data on the desired port. If it fails, do not spawn the vehicle
        # on the simulation world and just throw an exception
        try:
            self._mavlink = MavlinkInterface('tcpin:localhost:4560')
        except Exception as e:
            carb.log_error("Could not initiate the mavlink interface. Not spawning the vehicle. Full error log: ")
            carb.log_error(e)
        
        # Initiate the Vehicle
        super().__init__(stage_prefix, usd_file, world, init_pos, init_orientation)

        # Set the rotation direction from the propellers
        self.rot_dir = rot_dir

        # The actual rolling moment to apply to the body of the vehicle (rotate on yaw)
        self._rolling_moment = np.array([0.0, 0.0, 0.0])

        # Save the rolling moment coefficent used to compute how much torque to apply to
        # the body (yaw-rate)
        self._rolling_moment_coeficient = rolling_moment_coefficient

        # Create the sensors that a quadrotor typically has
        self._barometer = Barometer(altitude_home=488.0)                # Check
        self._imu = IMU()                                               # Check
        self._magnetometer = Magnetometer(47.397742, 8.545594)          # Check
        self._gps = GPS(47.397742, 8.545594, origin_altitude=488.0)     # Check
        self._linear_drag = LinearDrag(np.array([0.50, 0.30, 0.0]))
        
        # Add callbacks to the physics engine to update the sensors every timestep
        self._world.add_physics_callback(self._stage_prefix + "/barometer", self.update_barometer_sensor)
        self._world.add_physics_callback(self._stage_prefix + "/imu", self.update_imu_sensor)
        self._world.add_physics_callback(self._stage_prefix + "/magnetometer", self.update_magnetometer_sensor)
        self._world.add_physics_callback(self._stage_prefix + "/gps", self.update_gps_sensor)
        self._world.add_physics_callback(self._stage_prefix + "/mav_state", self.update_sim_state_mav)

        # Add a callback to start/stop the mavlink streaming once the play/stop button is hit
        self._world.add_timeline_callback(self._stage_prefix + "/start_stop_sim", self.sim_start_stop)

        self.total_time = 0

    def update_barometer_sensor(self, dt: float):
        self._mavlink.update_bar_data(self._barometer.update(self._state, dt))

    def update_imu_sensor(self, dt: float):
        self._mavlink.update_imu_data(self._imu.update(self._state, dt))

    def update_magnetometer_sensor(self, dt: float):
        self._mavlink.update_mag_data(self._magnetometer.update(self._state, dt))

    def update_gps_sensor(self, dt: float):
        self._mavlink.update_gps_data(self._gps.update(self._state, dt))

    def update_sim_state_mav(self, dt: float):
        self._mavlink.update_sim_state(self._state)

    def sim_start_stop(self, event):
        """
        Callback that is called every time there is a timeline event such as starting/stoping the simulation
        """
        
        # If the start/stop button was pressed, then start/stop mavlink communication
        if self._world.is_playing():
            self._mavlink.start_stream()
            pass

        if self._world.is_stopped():
            self._mavlink.stop_stream()
            pass

    def apply_forces(self, dt: float):
        """
        Method that computes and applies the forces to the vehicle in
        simulation based on the motor speed. This method must be implemented
        by a class that inherits this type
        """

        # Get the rotor frame interface of the vehicle (this will be the frame used to get the position, orientation, etc.)
        body = self._world.dc_interface.get_rigid_body(self._stage_prefix + "/vehicle/body")

        # Get the articulation root of the vehicle
        articulation = self._world.dc_interface.get_articulation(self._stage_prefix  + "/vehicle/body")

        # Get the force to apply to the body frame from mavlink
        forces_z = self._mavlink._rotor_data.input_force_reference

        # Apply force to each rotor
        for i in range(4):
            pass

            #Get the rotor frame interface of the vehicle (this will be the frame used to get the position, orientation, etc.)
            rotor = self._world.dc_interface.get_rigid_body(self._stage_prefix  + "/vehicle/rotor" + str(i))

            # Apply the force in Z on the rotor frame
            self._world.dc_interface.apply_body_force(rotor, carb._carb.Float3([0.0, 0.0, forces_z[i]]), carb._carb.Float3([ 0.0, 0.0, 0.0]), False)

            # Rotate the joint to yield the visual of a rotor spinning (for animation purposes only)
            joint = self._world.dc_interface.find_articulation_dof(articulation, "joint" + str(i))

            # Spinning when armed but not applying force
            if 0.0 < forces_z[i] < 0.1:
                self._world.dc_interface.set_dof_velocity(joint, 5 * self.rot_dir[i])
            # Spinning when armed and applying force
            elif 0.1 <= forces_z[i]:
                self._world.dc_interface.set_dof_velocity(joint, 100 * self.rot_dir[i])
            # Not spinning    
            else:
                self._world.dc_interface.set_dof_velocity(joint, 0)

        # Get the angular velocities of each individual rotor
        velocities = self._mavlink._rotor_data.input_reference

        # Define the axis of rotation of the joint where the rotors are located
        wind_vel = np.array([0.0, 0.0, 0.0])
        joint_axis_body = np.array([0.0, 0.0, 1.0])

        # Reset the rolling moment vector
        rolling_moment = 0.0

        # Compute the contributions for the rolling moment
        for i in range(4):

            # Compute the rolling moment coeficient
            motor_rolling_contrib = self._rolling_moment_coeficient * np.power(velocities[i], 2.0) * self.rot_dir[i]

            # Compute the torque to apply to the body of the vehicle based on the motor velocity (rolling moment)
            rolling_moment += motor_rolling_contrib

        self._world.dc_interface.apply_body_torque(body, carb._carb.Float3([0.0, 0.0, rolling_moment]), False)

        # Compute the total linear drag force to apply to the vehicle's body frame
        drag = self._linear_drag.update(self._state, dt)
        carb.log_warn(drag)
        self._world.dc_interface.apply_body_force(body, carb._carb.Float3(drag), carb._carb.Float3([0.0, 0.0, 0.0]), False)

        self.total_time += dt
        self._mavlink.mavlink_update(dt)
