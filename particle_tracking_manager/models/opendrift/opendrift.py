"""Using OpenDrift for particle tracking."""
import copy
import datetime
import gc
import json
import logging
import os
import platform
import tempfile
from pathlib import Path
from typing import Optional, Union

import appdirs
import pandas as pd
import xarray as xr
from opendrift.models.larvalfish import LarvalFish
from opendrift.models.leeway import Leeway
from opendrift.models.oceandrift import OceanDrift
from opendrift.models.openoil import OpenOil
from opendrift.readers import reader_ROMS_native

from ...cli import is_None
from ...config import OpenDriftConfig, _KNOWN_MODELS
from ...the_manager import ParticleTrackingManager
from ...config_logging import LoggerConfig
from .plot import check_plots, make_plots
from .utils import make_ciofs_kerchunk, make_nwgoa_kerchunk

class OpenDriftModel(ParticleTrackingManager):
    """Open drift particle tracking model.

    Defaults all come from config_model configuration file.

    Parameters
    ----------
    drift_model : str, optional
        Options: "OceanDrift", "LarvalFish", "OpenOil", "Leeway", by default "OceanDrift"
    export_variables : list, optional
        List of variables to export, by default None. See PTM docs for options.
    radius : int, optional
        Radius around each lon-lat pair, within which particles will be randomly seeded. This is used by function `seed_elements`.
    radius_type : str
        If 'gaussian' (default), the radius is the standard deviation in x-y-directions. If 'uniform', elements are spread evenly and always inside a circle with the given radius. This is used by function `seed_elements`.
    current_uncertainty : float
        Add gaussian perturbation with this standard deviation to current components at each time step.
    wind_uncertainty : float
        Add gaussian perturbation with this standard deviation to wind components at each time step.
    use_auto_landmask : bool
        Set as True to use general landmask instead of that from ocean_model.
        Use for testing primarily. Default is False.
    diffusivitymodel : str
        Algorithm/source used for profile of vertical diffusivity. Environment means that diffusivity is acquired from readers or environment constants/fallback. Turned on if ``vertical_mixing==True``.
    stokes_drift : bool, optional
        # TODO: move this to the relevant validator
        Set to True to turn on Stokes drift, by default True. This enables 3 settings in OpenDrift:

        * o.set_config('drift:use_tabularised_stokes_drift', True)
        * o.set_config('drift:tabularised_stokes_drift_fetch', '25000')  # default
        * o.set_config('drift:stokes_drift_profile', 'Phillips')  # default

        The latter two configurations are not additionally set in OpenDriftModel since they are already the default once stokes_drift is True.
    mixed_layer_depth : float
        Fallback value for ocean_mixed_layer_thickness if not available from any reader. This is used in the calculation of vertical diffusivity.
    coastline_action : str, optional
        Action to perform if a drifter hits the coastline, by default "previous". Options
        are 'stranding', 'previous'.
    seafloor_action : str, optional
        Action to perform if a drifter hits the seafloor, by default "deactivate". Options
        are 'deactivate', 'previous', 'lift_to_seafloor'.
    max_speed : int
        Typical maximum speed of elements, used to estimate reader buffer size.
    wind_drift_depth : float
        The direct wind drift (windage) is linearly decreasing from the surface value (wind_drift_factor) until 0 at this depth.
    vertical_mixing_timestep : float
        Time step used for inner loop of vertical mixing.
    object_type: str = config_model["object_type"]["default"],
        Leeway object category for this simulation.

    diameter : float
        Seeding value of diameter.
    neutral_buoyancy_salinity : float
        Seeding value of neutral_buoyancy_salinity.
    stage_fraction : float
        Seeding value of stage_fraction.
    hatched : float
        Seeding value of hatched.
    length : float
        Seeding value of length.
    weight : float
        Seeding value of weight.

    oil_type : str
        Oil type to be used for the simulation, from the NOAA ADIOS database.
    m3_per_hour : float
        The amount (volume) of oil released per hour (or total amount if release is instantaneous).
    oil_film_thickness : float
        Seeding value of oil_film_thickness.
    droplet_size_distribution : str
        Droplet size distribution used for subsea release.
    droplet_diameter_mu : float
        The mean diameter of oil droplet for a subsea release, used in normal/lognormal distributions.
    droplet_diameter_sigma : float
        The standard deviation in diameter of oil droplet for a subsea release, used in normal/lognormal distributions.
    droplet_diameter_min_subsea : float
        The minimum diameter of oil droplet for a subsea release, used in uniform distribution.
    droplet_diameter_max_subsea : float
        The maximum diameter of oil droplet for a subsea release, used in uniform distribution.
    emulsification : bool
        Surface oil is emulsified, i.e. water droplets are mixed into oil due to wave mixing, with resulting increase of viscosity.
    dispersion : bool
        Oil is removed from simulation (dispersed), if entrained as very small droplets.
    evaporation : bool
        Surface oil is evaporated.
    update_oilfilm_thickness : bool
        Oil film thickness is calculated at each time step. The alternative is that oil film thickness is kept constant with value provided at seeding.
    biodegradation : bool
        Oil mass is biodegraded (eaten by bacteria).
    plots : dict, optional
        Dictionary of plot names, their filetypes, and any kwargs to pass along, by default None.
        Available plot names are "spaghetti", "animation", "oil", "all".

    Notes
    -----
    Docs available for more initialization options with ``ptm.ParticleTrackingManager?``

    """
    # TODO: is kwargs needed in init? test this.
    def __init__(self, **kwargs):
        """Initialize OpenDriftModel."""
        # TODO: I think there is no reason to have "model" defined in the_manager_config.json since the 
        # model object is used to instantiate the combined object.
        
        # OpenDriftConfig, _KNOWN_MODELS = setup_opendrift_config(**kwargs)
        
        # Initialize the parent class
        # This sets up the logger and ParticleTrackingState.
        super().__init__(**kwargs)
        
        # OpenDriftConfig is a subclass of PTMConfig so it knows about all the
        # PTMConfig parameters. PTMConfig is run with OpenDriftConfig.
        # output_file was altered in PTM when setting up logger, so want to use
        # that version.
        kwargs.update({"output_file": self.output_file})
        self.config = OpenDriftConfig(**kwargs)

        self._setup_interpolator()

        # model = "opendrift"

        # I left this code here but it isn't used for now
        # it will be used if we can export to parquet/netcdf directly
        # without needing to resave the file with extra config
        # # need output_format defined right away
        # self.__dict__["output_format"] = output_format

        LoggerConfig().merge_with_opendrift_log(self.logger)
        
        self._create_opendrift_model_object()
        self._modify_opendrift_model_object()

        # # Extra keyword parameters are not currently allowed so they might be a typo
        # if len(self.kw) > 0:
        #     raise KeyError(f"Unknown input parameter(s) {self.kw} input.")

        # Note that you can see configuration possibilities for a given model with
        # o.list_configspec()
        # You can check the metadata for a given configuration with (min/max/default/type)
        # o.get_configspec('vertical_mixing:timestep')
        # You can check required variables for a model with
        # o.required_variables

        # TODO: streamline this
        self.checked_plot = False
        
        # TODO: move ocean_model setup to another function/class
        # TODO: setup OpenDrift config and how to blend with this config


    def _setup_interpolator(self):
        """Setup interpolator."""
        if self.config.use_cache:
            cache_dir = Path(appdirs.user_cache_dir(appname="particle-tracking-manager", appauthor="axiom-data-science"))
            cache_dir.mkdir(parents=True, exist_ok=True)
            if self.config.interpolator_filename is None:
                self.config.interpolator_filename = cache_dir / Path(f"{self.config.ocean_model}_interpolator").with_suffix(".pickle")
            else:
                self.config.interpolator_filename = Path(self.config.interpolator_filename).with_suffix(".pickle")
            self.save_interpolator = True
            
            # change interpolator_filename to string
            self.config.interpolator_filename = str(self.config.interpolator_filename)
            
            if Path(self.config.interpolator_filename).exists():
                self.logger.info(f"Loading the interpolator from {self.config.interpolator_filename}.")
            else:
                self.logger.info(f"A new interpolator will be saved to {self.config.interpolator_filename}.")
        else:
            self.save_interpolator = False
            self.logger.info("Interpolators will not be saved.")

    def _create_opendrift_model_object(self):
        # do this right away so I can query the object
        # we don't actually input output_format here because we first output to netcdf, then
        # resave as parquet after adding in extra config
        # TODO: should drift_model be instantiated in OpenDriftConfig or here?
        if self.config.drift_model == "Leeway":
            # getattr(logging, self.config.log_level) converts from, e.g., "INFO" to 20
            o = Leeway(loglevel=getattr(logging, self.config.log_level))  # , output_format=self.output_format)

        elif self.config.drift_model == "OceanDrift":
            o = OceanDrift(
                loglevel=getattr(logging, self.config.log_level),
            )  # , output_format=self.output_format)

        elif self.config.drift_model == "LarvalFish":
            o = LarvalFish(
                loglevel=getattr(logging, self.config.log_level)
            )  # , output_format=self.output_format)

        elif self.config.drift_model == "OpenOil":
            o = OpenOil(
                loglevel=getattr(logging, self.config.log_level), weathering_model="noaa"
            )  # , output_format=self.output_format)

        else:
            raise ValueError(f"Drifter model {self.config.drift_model} is not recognized.")
        # TODO: Should I keep this sort of ValueError when the input parameter has already been validated?
        
        self.o = o

    def _modify_opendrift_model_object(self):
        
        # TODO: where to put these things
        # turn on other things if using stokes_drift
        if self.config.stokes_drift:
            self.o.set_config("drift:use_tabularised_stokes_drift", True)
            # self.o.set_config('drift:tabularised_stokes_drift_fetch', '25000')  # default
            # self.o.set_config('drift:stokes_drift_profile', 'Phillips')  # default

        # If 2D surface simulation (and not Leeway since not available), truncate model output below 0.5 m
        if not self.config.do3D and self.config.z == 0 and self.config.drift_model != "Leeway":
            self.o.set_config("drift:truncate_ocean_model_below_m", 0.5)
            self.logger.info("Truncating model output below 0.5 m.")


        # If 2D simulation (and not Leeway since not available), turn off vertical advection
        if not self.config.do3D and self.config.drift_model != "Leeway":
            self.o.set_config("drift:vertical_advection", False)
            self.logger.info("Disabling vertical advection.")

        # If 3D simulation, turn on vertical advection
        if self.config.do3D:
            self.o.set_config("drift:vertical_advection", True)
            self.logger.info("do3D is True so turning on vertical advection.")


    def add_reader(
        self,
        ds=None,
        name=None,
        oceanmodel_lon0_360=False,
        standard_name_mapping=None,
    ):
        """Might need to cache this if its still slow locally.

        Parameters
        ----------
        ds : xr.Dataset, optional
            Previously-opened Dataset containing ocean model output, if user wants to input
            unknown reader information.
        name : str, optional
            If ds is input, user can also input name of ocean model, otherwise will be called "user_input".
        oceanmodel_lon0_360 : bool
            True if ocean model longitudes span 0 to 360 instead of -180 to 180.
        standard_name_mapping : dict
            Mapping of model variable names to standard names.
        """

        if (
            self.config.ocean_model not in _KNOWN_MODELS
            and self.config.ocean_model != "test"
            and ds is None
        ):
            raise ValueError(
                "ocean_model must be a known model or user must input a Dataset."
            )

        standard_name_mapping = standard_name_mapping or {}

        if ds is not None:
            if name is None:
                self.config.ocean_model = "user_input"
            else:
                self.config.ocean_model = name

        if self.config.ocean_model == "test":
            pass
            # oceanmodel_lon0_360 = True
            # loc = "test"
            # kwargs_xarray = dict()

        elif self.config.ocean_model is not None or ds is not None:
            
            # TODO: should I change to computed_fields and where should this go?

            # set drop_vars initial values based on the PTM settings, then add to them for the specific model
            drop_vars = []
            # don't need w if not 3D movement
            if not self.config.do3D:
                drop_vars += ["w"]
                self.logger.info("Dropping vertical velocity (w) because do3D is False")
            else:
                self.logger.info("Retaining vertical velocity (w) because do3D is True")

            # don't need winds if stokes drift, wind drift, added wind_uncertainty, and vertical_mixing are off
            # It's possible that winds aren't required for every OpenOil simulation but seems like
            # they would usually be required and the cases are tricky to navigate so also skipping for that case.
            if (
                not self.config.stokes_drift
                and self.config.wind_drift_factor == 0
                and self.config.wind_uncertainty == 0
                and self.config.drift_model != "OpenOil"
                and not self.config.vertical_mixing
            ):
                drop_vars += ["Uwind", "Vwind", "Uwind_eastward", "Vwind_northward"]
                self.logger.info(
                    "Dropping wind variables because stokes_drift, wind_drift_factor, wind_uncertainty, and vertical_mixing are all off and drift_model is not 'OpenOil'"
                )
            else:
                self.logger.info(
                    "Retaining wind variables because stokes_drift, wind_drift_factor, wind_uncertainty, or vertical_mixing are on or drift_model is 'OpenOil'"
                )

            # only keep salt and temp for LarvalFish or OpenOil
            if self.config.drift_model not in ["LarvalFish", "OpenOil"]:
                drop_vars += ["salt", "temp"]
                self.logger.info(
                    "Dropping salt and temp variables because drift_model is not LarvalFish nor OpenOil"
                )
            else:
                self.logger.info(
                    "Retaining salt and temp variables because drift_model is LarvalFish or OpenOil"
                )

            # keep some ice variables for OpenOil (though later see if these are used)
            if self.config.drift_model != "OpenOil":
                drop_vars += ["aice", "uice_eastward", "vice_northward"]
                self.logger.info(
                    "Dropping ice variables because drift_model is not OpenOil"
                )
            else:
                self.logger.info(
                    "Retaining ice variables because drift_model is OpenOil"
                )

            # if using static masks, drop wetdry masks.
            # if using wetdry masks, drop static masks.
            if self.config.use_static_masks:
                standard_name_mapping.update({"mask_rho": "land_binary_mask"})
                drop_vars += ["wetdry_mask_rho", "wetdry_mask_u", "wetdry_mask_v"]
                self.logger.info(
                    "Dropping wetdry masks because using static masks instead."
                )
            else:
                standard_name_mapping.update({"wetdry_mask_rho": "land_binary_mask"})
                drop_vars += ["mask_rho", "mask_u", "mask_v", "mask_psi"]
                self.logger.info(
                    "Dropping mask_rho, mask_u, mask_v, mask_psi because using wetdry masks instead."
                )

            if self.config.ocean_model == "NWGOA":
                oceanmodel_lon0_360 = True

                standard_name_mapping.update(
                    {
                        "u_eastward": "x_sea_water_velocity",
                        "v_northward": "y_sea_water_velocity",
                        # NWGOA, there are east/north oriented and will not be rotated
                        # because "east" "north" in variable names
                        "Uwind_eastward": "x_wind",
                        "Vwind_northward": "y_wind",
                    }
                )

                # remove all other grid masks because variables are all on rho grid
                drop_vars += [
                    "hice",
                    "hraw",
                    "snow_thick",
                ]

                if self.config.ocean_model_local:

                    if self.config.start_time is None:
                        raise ValueError(
                            "Need to set start_time ahead of time to add local reader."
                        )
                    start_time = self.config.start_time
                    start = f"{start_time.year}-{str(start_time.month).zfill(2)}-{str(start_time.day).zfill(2)}"
                    end_time = self.config.end_time
                    end = f"{end_time.year}-{str(end_time.month).zfill(2)}-{str(end_time.day).zfill(2)}"
                    loc_local = make_nwgoa_kerchunk(start=start, end=end)

                # loc_local = "/mnt/depot/data/packrat/prod/aoos/nwgoa/processed/nwgoa_kerchunk.parq"
                loc_remote = (
                    "http://xpublish-nwgoa.srv.axds.co/datasets/nwgoa_all/zarr/"
                )

            elif "CIOFS" in self.config.ocean_model:
                oceanmodel_lon0_360 = False

                drop_vars += [
                    "wetdry_mask_psi",
                ]
                if self.config.ocean_model == "CIOFS":

                    if self.config.ocean_model_local:

                        if self.config.start_time is None:
                            raise ValueError(
                                "Need to set start_time ahead of time to add local reader."
                            )
                        start = f"{self.config.start_time.year}_{str(self.config.start_time.dayofyear - 1).zfill(4)}"
                        end = f"{self.config.end_time.year}_{str(self.config.end_time.dayofyear).zfill(4)}"
                        loc_local = make_ciofs_kerchunk(
                            start=start, end=end, name="ciofs"
                        )
                    loc_remote = "http://xpublish-ciofs.srv.axds.co/datasets/ciofs_hindcast/zarr/"

                elif self.config.ocean_model == "CIOFSFRESH":

                    if self.config.ocean_model_local:

                        if self.config.start_time is None:
                            raise ValueError(
                                "Need to set start_time ahead of time to add local reader."
                            )
                        start = f"{self.config.start_time.year}_{str(self.config.start_time.dayofyear - 1).zfill(4)}"

                        end = f"{self.config.end_time.year}_{str(self.config.end_time.dayofyear).zfill(4)}"
                        loc_local = make_ciofs_kerchunk(
                            start=start, end=end, name="ciofs_fresh"
                        )
                    loc_remote = None

                elif self.config.ocean_model == "CIOFSOP":

                    standard_name_mapping.update(
                        {
                            "u_eastward": "x_sea_water_velocity",
                            "v_northward": "y_sea_water_velocity",
                        }
                    )

                    if self.config.ocean_model_local:

                        if self.config.start_time is None:
                            raise ValueError(
                                "Need to set start_time ahead of time to add local reader."
                            )
                        start = f"{self.config.start_time.year}-{str(self.config.start_time.month).zfill(2)}-{str(self.config.start_time.day).zfill(2)}"
                        end = f"{self.config.end_time.year}-{str(self.config.end_time.month).zfill(2)}-{str(self.config.end_time.day).zfill(2)}"

                        loc_local = make_ciofs_kerchunk(
                            start=start, end=end, name="aws_ciofs_with_angle"
                        )
                        # loc_local = "/mnt/depot/data/packrat/prod/noaa/coops/ofs/aws_ciofs/processed/aws_ciofs_kerchunk.parq"

                    loc_remote = "https://thredds.aoos.org/thredds/dodsC/AWS_CIOFS.nc"

            elif self.config.ocean_model == "user_input":

                # check for case that self.config.use_static_masks False (which is the default)
                # but user input doesn't have wetdry masks
                # then raise exception and tell user to set use_static_masks True
                if "wetdry_mask_rho" not in ds.data_vars and not self.config.use_static_masks:
                    raise ValueError(
                        "User input does not have wetdry_mask_rho variable. Set use_static_masks True to use static masks instead."
                    )

                ds = ds.drop_vars(drop_vars, errors="ignore")

            # if local and not a user-input ds
            if ds is None:
                if self.config.ocean_model_local:

                    ds = xr.open_dataset(
                        loc_local,
                        engine="kerchunk",
                        # chunks={},  # Looks like it is faster not to include this for kerchunk
                        drop_variables=drop_vars,
                        decode_times=False,
                    )

                    self.logger.info(
                        f"Opened local dataset starting {start} and ending {end} with number outputs {ds.ocean_time.size}."
                    )

                # otherwise remote
                else:
                    if ".nc" in loc_remote:

                        if self.config.ocean_model == "CIOFSFRESH":
                            raise NotImplementedError

                        ds = xr.open_dataset(
                            loc_remote,
                            chunks={},
                            drop_variables=drop_vars,
                            decode_times=False,
                        )
                    else:
                        ds = xr.open_zarr(
                            loc_remote,
                            chunks={},
                            drop_variables=drop_vars,
                            decode_times=False,
                        )

                    self.logger.info(
                        f"Opened remote dataset {loc_remote} with number outputs {ds.ocean_time.size}."
                    )

            # For NWGOA, need to calculate wetdry mask from a variable
            if self.config.ocean_model == "NWGOA" and not self.config.use_static_masks:
                ds["wetdry_mask_rho"] = (~ds.zeta.isnull()).astype(int)

            # For CIOFSOP need to rename u/v to have "East" and "North" in the variable names
            # so they aren't rotated in the ROMS reader (the standard names have to be x/y not east/north)
            elif self.config.ocean_model == "CIOFSOP":
                ds = ds.rename_vars({"urot": "u_eastward", "vrot": "v_northward"})
                # grid = xr.open_dataset("/mnt/vault/ciofs/HINDCAST/nos.ciofs.romsgrid.nc")
                # ds["angle"] = grid["angle"]

            try:
                units = ds.ocean_time.attrs["units"]
            except KeyError:
                units = ds.ocean_time.encoding["units"]
            datestr = units.split("since ")[1]
            units_date = pd.Timestamp(datestr)

            # use reader start time if not otherwise input
            if self.config.start_time is None:
                self.logger.info("setting reader start_time as simulation start_time")
                # self.config.start_time = reader.start_time
                # convert using pandas instead of netCDF4
                self.config.start_time = units_date + pd.to_timedelta(
                    ds.ocean_time[0].values, unit="s"
                )
            # narrow model output to simulation time if possible before sending to Reader
            if self.config.start_time is not None and self.config.end_time is not None:
                dt_model = float(
                    ds.ocean_time[1] - ds.ocean_time[0]
                )  # time step of the model output in seconds
                # want to include the next ocean model output before the first drifter simulation time
                # in case it starts before model times
                start_time_num = (
                    self.config.start_time - units_date
                ).total_seconds() - dt_model
                # want to include the next ocean model output after the last drifter simulation time
                end_time_num = (self.config.end_time - units_date).total_seconds() + dt_model
                ds = ds.sel(ocean_time=slice(start_time_num, end_time_num))
                self.logger.info("Narrowed model output to simulation time")
                if len(ds.ocean_time) == 0:
                    raise ValueError(
                        "No model output left for simulation time. Check start_time and end_time."
                    )
                if len(ds.ocean_time) == 1:
                    raise ValueError(
                        "Only 1 model output left for simulation time. Check start_time and end_time."
                    )
            else:
                raise ValueError(
                    "start_time and end_time must be set to narrow model output to simulation time"
                )

            reader = reader_ROMS_native.Reader(
                filename=ds,
                name=self.config.ocean_model,
                standard_name_mapping=standard_name_mapping,
                save_interpolator=self.save_interpolator,
                interpolator_filename=self.interpolator_filename,
            )

            self.o.add_reader([reader])
            self.reader = reader
            # can find reader at manager.o.env.readers[self.config.ocean_model]

            self.oceanmodel_lon0_360 = oceanmodel_lon0_360

        else:
            raise ValueError("reader did not set an ocean_model")
        
        self.state.has_added_reader = True


    @property
    def seed_kws(self):
        """Gather seed input kwargs.

        This could be run more than once.
        """

        already_there = [
            "seed:number",
            "seed:z",
            "seed:seafloor",
            "seed:droplet_diameter_mu",
            "seed:droplet_diameter_min_subsea",
            "seed:droplet_size_distribution",
            "seed:droplet_diameter_sigma",
            "seed:droplet_diameter_max_subsea",
            "seed:object_type",
            "seed_flag",
            "drift:use_tabularised_stokes_drift",
            "drift:vertical_advection",
            "drift:truncate_ocean_model_below_m",
        ]

        if self.config.start_time_end is not None:
            # time can be a list to start drifters linearly in time
            time = [
                self.config.start_time.to_pydatetime(),
                self.config.start_time_end.to_pydatetime(),
            ]
        elif self.config.start_time is not None:
            time = self.config.start_time.to_pydatetime()
        else:
            time = None

        _seed_kws = {
            "time": time,
            "z": self.config.z,
        }

        # update seed_kws with drift_model-specific seed parameters
        seedlist = self.drift_model_config(prefix="seed")
        seedlist = [(one, two) for one, two in seedlist if one not in already_there]
        seedlist = [(one.replace("seed:", ""), two) for one, two in seedlist]
        _seed_kws.update(seedlist)

        if self.seed_flag == "elements":
            # add additional seed parameters
            _seed_kws.update(
                {
                    "lon": self.config.lon,
                    "lat": self.config.lat,
                    "radius": self.config.radius,
                    "radius_type": self.config.radius_type,
                }
            )

        elif self.seed_flag == "geojson":

            # geojson needs string representation of time
            _seed_kws["time"] = (
                self.config.start_time.isoformat() if self.config.start_time is not None else None
            )

        self._seed_kws = _seed_kws
        return self._seed_kws


    def seed(self):
        """Actually seed drifters for model."""

        if not self.state.has_added_reader:
            raise ValueError("first add reader with `manager.add_reader(**kwargs)`.")

        if self.config.seed_flag == "elements":
            self.o.seed_elements(**self.seed_kws)

        elif self.seed_flag == "geojson":

            # # geojson needs string representation of time
            # self.seed_kws["time"] = self.config.start_time.isoformat()
            self.geojson["properties"] = self.seed_kws
            json_string_dumps = json.dumps(self.geojson)
            self.o.seed_from_geojson(json_string_dumps)

        else:
            raise ValueError(f"seed_flag {self.config.seed_flag} not recognized.")

        self.initial_drifters = self.o.elements_scheduled

        self.state.has_run_seeding = True

    def run(self):
        """Run the drifters!"""

        if not self.state.has_run_seeding:
            raise ValueError("first run seeding with `manager.seed()`.")

        self.logger.info(f"start_time: {self.config.start_time}, end_time: {self.config.end_time}, steps: {self.config.steps}, duration: {self.config.duration}")

        # if self.steps is None and self.duration is None and self.config.end_time is None:
        #     raise ValueError(
        #         "Exactly one of steps, duration, or end_time must be input and not be None."
            # )

        # if self.run_forward:
        #     timedir = 1
        # else:
        #     timedir = -1

        # drop non-OpenDrift parameters now so they aren't brought into simulation (they mess up the write step)
        full_config = copy.deepcopy(self._config)  # save
        config_input_to_opendrift = {
            k: full_config[k] for k in self._config_orig.keys()
        }

        self.o._config = config_input_to_opendrift  # only OpenDrift config

        # initially output to netcdf even if parquet has been selected
        # since I do this weird 2 step saving process

        # if self.output_format == "netcdf":
        #     output_file_initial += ".nc"
        # elif self.output_format == "parquet":
        #     output_file_initial += ".parq"
        # else:
        #     raise ValueError(f"output_format {self.output_format} not recognized.")

        self.o.run(
            time_step=self.config.time_step,
            time_step_output=self.config.time_step_output,
            steps=self.config.steps,
            export_variables=self.config.export_variables,
            outfile=self.config.output_file_initial,
        )

        # plot if requested
        if self.plots:
            # return plots because now contains the filenames for each plot
            self.plots = make_plots(
                self.plots, self.o, self.output_file.split(".")[0], self.drift_model
            )

            # convert plots dict into string representation to save in output file attributes
            # https://github.com/pydata/xarray/issues/1307
            self.plots = repr(self.plots)

        self.o._config = full_config  # reinstate config

        # open outfile file and add config to it
        # config can't be present earlier because it breaks opendrift
        ds = xr.open_dataset(self.output_file_initial)
        for k, v in self.drift_model_config():
            if isinstance(v, (bool, type(None), pd.Timestamp, pd.Timedelta)):
                v = str(v)
            ds.attrs[f"ptm_config_{k}"] = v

        if self.config.output_format == "netcdf":
            ds.to_netcdf(self.config.output_file)
        elif self.config.output_format == "parquet":
            ds.to_dataframe().to_parquet(self.config.output_file)
        else:
            raise ValueError(f"output_format {self.config.output_format} not recognized.")

        # update with new path name
        self.o.outfile_name = self.output_file
        # self.output_file = self.output_file

        try:
            # remove initial file to save space
            os.remove(self.output_file_initial)
        except PermissionError:
            # windows issue
            pass

        LoggerConfig().close_loggers(self.logger)
        # self.logger.removeHandler(self.logger.handlers[0])
        # self.logger.handlers[0].close()
        self.state.has_run = True


    @property
    def _config(self):
        """Surface the model configuration."""

        # save for reinstatement when running the drifters
        if self._config_orig is None:
            self._config_orig = copy.deepcopy(self.o._config)

        return self.o._config

    # # dictC is the same as dictB except the names are the PTM/OpenDriftModel names instead of the
    # # original OpenDrift names
    # def _add_ptm_config(self):
    #     """Add PTM config to overall config."""

    #     dict1 = copy.deepcopy(self._config)
    #     dict2 = copy.deepcopy(self.config_ptm)

    #     # dictB has the od_mapping version of the keys of dict2
    #     # e.g.  'processes:emulsification' instead of 'emulsification'
    #     # dictB values are the OpenDriftModel config parameters with config_od parameters added on top
    #     dictB = {
    #         v["od_mapping"]: (
    #             {**dict1[v["od_mapping"]], **v}
    #             if "od_mapping" in v and v["od_mapping"] in dict1.keys()
    #             else {**v}
    #         )
    #         for k, v in dict2.items()
    #         if "od_mapping" in v
    #     }

    #     # dictC is the same as dictB except the names are the PTM/OpenDriftModel names instead of the
    #     # original OpenDrift names
    #     dictC = {
    #         k: {**dict1[v["od_mapping"]], **v}
    #         if "od_mapping" in v and v["od_mapping"] in dict1.keys()
    #         else {**v}
    #         for k, v in dict2.items()
    #         if "od_mapping" in v
    #     }

    #     # this step copies in parameter info from config_ptm to _config
    #     self._config.update(dict2)

    #     # this step brings config overrides from config_ptm into the overall config
    #     self._config.update(dictB)
    #     # this step brings other model config (plus additions from mapped parameter config) into the overall config
    #     self._config.update(dictC)
    #     # # this step brings other model config into the overall config
    #     # self._config.update(dict2)

    # def _add_model_config(self):
    #     """Goal is to combine the config both directions:

    #     * override OpenDrift config defaults with those from opendrift_config as well
    #       as include extra information like ptm_level
    #     * bring OpenDrift config parameter metadata into config_model so application
    #       could query it to get the ranges, options, etc.
    #     """

    #     dict1 = copy.deepcopy(self._config)
    #     dict2 = copy.deepcopy(self.config_model)

    #     # dictB has the od_mapping version of the keys of dict2
    #     # e.g.  'processes:emulsification' instead of 'emulsification'
    #     # dictB values are the OpenDrift config parameters with config_od parameters added on top
    #     dictB = {
    #         v["od_mapping"]: {**dict1[v["od_mapping"]], **v}
    #         if "od_mapping" in v and v["od_mapping"] in dict1.keys()
    #         else {**v}
    #         for k, v in dict2.items()
    #         if "od_mapping" in v
    #     }

    #     # dictC is the same as dictB except the names are the PTM/OpenDriftModel names instead of the
    #     # original OpenDrift names
    #     dictC = {
    #         k: {**dict1[v["od_mapping"]], **v}
    #         if "od_mapping" in v and v["od_mapping"] in dict1.keys()
    #         else {**v}
    #         for k, v in dict2.items()
    #         if "od_mapping" in v
    #     }

    #     # this step copies in parameter info from config_ptm to _config
    #     self._config.update(dict2)

    #     # this step brings config overrides from config_model into the overall config
    #     self._config.update(dictB)
    #     # this step brings other model config (plus additions from mapped parameter config) into the overall config
    #     self._config.update(dictC)

    def all_export_variables(self):
        """Output list of all possible export variables."""

        vars = (
            list(self.o.elements.variables.keys())
            + ["trajectory", "time"]
            + list(self.o.required_variables.keys())
        )

        return vars

    def export_variables(self):
        """Output list of all actual export variables."""

        return self.o.export_variables

    def drift_model_config(self, ptm_level=[1, 2, 3], prefix=""):
        """Show config for this drift model selection.

        This shows all PTM-controlled parameters for the OpenDrift
        drift model selected and their current values, at the selected ptm_level
        of importance. It includes some additional configuration parameters
        that are indirectly controlled by PTM parameters.

        Parameters
        ----------
        ptm_level : int, list, optional
            Options are 1, 2, 3, or lists of combinations. Use [1,2,3] for all.
            Default is 1.
        prefix : str, optional
            prefix to search config for, only for OpenDrift parameters (not PTM).
        """

        outlist = [
            (key, value_dict["value"])
            for key, value_dict in self.show_config(
                substring=":", ptm_level=ptm_level, level=[1, 2, 3], prefix=prefix
            ).items()
            if "value" in value_dict and value_dict["value"] is not None
        ]

        # also PTM config parameters that are separate from OpenDrift parameters
        outlist2 = [
            (key, value_dict["value"])
            for key, value_dict in self.show_config(
                ptm_level=ptm_level, prefix=prefix
            ).items()
            if "od_mapping" not in value_dict
            and "value" in value_dict
            and value_dict["value"] is not None
        ]

        # extra parameters that are not in the config_model but are set by PTM indirectly
        extra_keys = [
            "drift:vertical_advection",
            "drift:truncate_ocean_model_below_m",
            "drift:use_tabularised_stokes_drift",
        ]
        outlist += [
            (key, self.show_config(key=key)["value"])
            for key in extra_keys
            if "value" in self.show_config(key=key)
        ]

        return outlist + outlist2

    def get_configspec(self, prefix, substring, excludestring, level, ptm_level):
        """Copied from OpenDrift, then modified."""

        if not isinstance(level, list) and level is not None:
            level = [level]
        if not isinstance(ptm_level, list) and ptm_level is not None:
            ptm_level = [ptm_level]

        # check for prefix or substring comparison
        configspec = {
            k: v
            for (k, v) in self._config.items()
            if k.startswith(prefix) and substring in k and excludestring not in k
        }

        if level is not None:
            # check for levels (if present)
            configspec = {
                k: v
                for (k, v) in configspec.items()
                if "level" in configspec[k] and configspec[k]["level"] in level
            }

        if ptm_level is not None:
            # check for ptm_levels (if present)
            configspec = {
                k: v
                for (k, v) in configspec.items()
                if "ptm_level" in configspec[k]
                and configspec[k]["ptm_level"] in ptm_level
            }

        return configspec

    def show_config_model(
        self,
        key=None,
        prefix="",
        level=None,
        ptm_level=None,
        substring="",
        excludestring="excludestring",
    ) -> dict:
        """Show configuring for the drift model selected in configuration.

        Runs configuration for you if it hasn't yet been run.

        Parameters
        ----------
        key : str, optional
            If input, show configuration for just that key.
        prefix : str, optional
            prefix to search config for, only for OpenDrift parameters (not PTM).
        level : int, list, optional
            Limit search by level:

            * CONFIG_LEVEL_ESSENTIAL = 1
            * CONFIG_LEVEL_BASIC = 2
            * CONFIG_LEVEL_ADVANCED = 3

            e.g. 1, [1,2], [1,2,3]
        ptm_level : int, list, optional
            Limit search by level:

            * Surface to user = 1
            * Medium surface to user = 2
            * Surface but bury = 3

            e.g. 1, [1,2], [1,2,3]. To access all PTM parameters search for
            `ptm_level=[1,2,3]`.
        substring : str, optional
            If input, show configuration that contains that substring.
        excludestring : str, optional
            configuration parameters are not shown if they contain this string.

        Examples
        --------
        Show all possible configuration for the previously-selected drift model:

        >>> manager.show_config()

        Show configuration with a specific prefix:

        >>> manager.show_config(prefix="seed")

        Show configuration matching a substring:

        >>> manager.show_config(substring="stokes")

        Show configuration at a specific level (from OpenDrift):

        >>> manager.show_config(level=1)

        Show all OpenDrift configuration:

        >>> manager.show_config(level=[1,2,3])

        Show configuration for only PTM-specified parameters:

        >>> manager.show_config(ptm_level=[1,2,3])

        Show configuration for a specific PTM level:

        >>> manager.show_config(ptm_level=2)

        Show configuration for a single key:

        >>> manager.show_config("seed:oil_type")

        Show configuration for parameters that are both OpenDrift and PTM-modified:

        >>> m.show_config(ptm_level=[1,2,3], level=[1,2,3])

        """

        if key is not None:
            prefix = key

        output = self.get_configspec(
            prefix=prefix,
            level=level,
            ptm_level=ptm_level,
            substring=substring,
            excludestring=excludestring,
        )

        if key is not None:
            if key in output:
                return output[key]
            else:
                return output
        else:
            return output

    def reader_metadata(self, key):
        """allow manager to query reader metadata."""

        if not self.state.has_added_reader:
            raise ValueError("reader has not been added yet.")
        return self.o.env.readers[self.config.ocean_model].__dict__[key]

    # @property
    # def outfile_name(self):
    #     """Output file name."""

    #     return self.o.outfile_name
