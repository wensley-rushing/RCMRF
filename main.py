"""
Master file to run OpenSees
3D model may be used with hingeModel = Hysteretic only - haselton to be adapted
It is recommended to run each analysis separately with the order being:
1. Static analysis - ST
2. Modal analysis - MA
3. Static pushover analysis - PO
4. Nonlinear time history analysis - TH (supports IDA only for now)

Requires 3-4 input files
Materials file for concrete and reinforcement properties
Action file defining gravity loads and pdelta loads (not necessary for 3D)
Hinge model information in primary direction
Hinge model information in secondary direction (not necessary for 2D)
Hinge model information for gravity frame elements (not necessary for 2D)
"""
import openseespy.opensees as op

from analysis.multiStripeAnalysis import get_records
from client.model import Model
from analysis.static import Static
from pathlib import Path
import numpy as np
import os
import json
import pickle
from analysis.ida_htf_3d import IDA_HTF_3D
from client.modelToTCL import ModelToTCL
from utils.utils import createFolder, get_time, get_start_time


class Main:
    def __init__(self, sections_file, loads_file, materials_file, outputsDir, gmdir=None, gmfileNames=None, IM_type=2,
                 max_runs=15, analysis_time_step=.01, drift_capacity=10., analysis_type=None, system="Space",
                 hinge_model="Hysteretic", flag3d=False, direction=0, export_at_each_step=True,
                 period_assignment=None, periods_ida=None, tcl_filename=None, modal_analysis_path=None):
        """
        Initializes master file
        :param sections_file: str                   Name of file containing section data in '*.csv' format
        :param loads_file: str                      Name of file containing load data in '*.csv' format
        :param materials_file: dict                 Concrete and reinforcement material properties
        :param outputsDir: str                      Directory for exporting the analyses results
        :param gmdir: str                           Ground motion directory
        :param gmfileNames: list(str)               Files containing information on ground motion files (check sample)
        :param IM_type: int                         Intensity measure tag for IDA
        :param max_runs: int                        Maximum number of runs for IDA
        :param analysis_time_step: float            Analysis time step for IDA
        :param drift_capacity: float                Drift capacity in % for stopping IDA
        :param analysis_type: list(str)             Type of analysis for which we are recording [TH, PO, ST, MA, ELF]
                                                    TH - time history analysis
                                                    PO - static pushover analysis
                                                    ST - static analysis (e.g. gravity)
                                                    MA - modal analysis
                                                    ELF - equivalent lateral force method of analysis
        :param system: str                          System type (e.g. Perimeter or Space)
        :param hinge_model: str                     Hinge model (hysteretic or haselton)
        :param flag3d: bool                         True for 3D modelling, False for 2D modelling
        :param direction: int                       Direction of analysis
        :param export_at_each_step: bool            Exporting at each step, i.e. record-run
        :param period_assignment: dict              Period assignment IDs (for 3D only)
        :param periods_ida: list                    Periods to use for IDA, optional, in case MA periods are not needed
        :param tcl_filename: str                    TCL filename to export to
        :param modal_analysis_path: Path            Path to modal_analysis_results.json
        """
        # TODO, add support for Haselton, currently only a placeholder, need to adapt for 3D etc.
        # list of strings for 3D modelling, and string for 2D modelling
        self.sections_file = sections_file

        # Input arguments
        self.outputsDir = outputsDir
        self.loads_file = loads_file
        self.materials_file = materials_file
        self.gmdir = gmdir
        self.gmfileNames = gmfileNames
        self.IM_type = IM_type
        self.max_runs = max_runs
        self.analysis_time_step = analysis_time_step
        self.drift_capacity = drift_capacity
        self.analysis_type = analysis_type
        self.system = system
        self.hinge_model = hinge_model.lower()
        self.flag3d = flag3d
        self.export_at_each_step = export_at_each_step
        self.direction = direction
        self.period_assignment = period_assignment
        self.periods_ida = periods_ida
        self.tcl_filename = tcl_filename

        if modal_analysis_path:
            self.modal_analysis_path = modal_analysis_path
        else:
            self.modal_analysis_path = self.outputsDir / "MA.json"

        # Constants
        self.APPLY_GRAVITY_ELF = False
        self.FIRST_INT = .05
        self.INCR_STEP = .05
        self.DAMPING = .05

        # Records for MSA
        self.records = None

        # Create an outputs directory if none exists
        createFolder(outputsDir)

        if direction != 0 and flag3d and analysis_type == "MA":
            print("[WARNING] Direction should be set to 0 for Modal Analysis!")

    @staticmethod
    def wipe():
        """
        Perform a clean wipe
        :return: None
        """
        op.wipe()

    def call_model(self, generate_model=True):
        """
        Calls the Model
        :param generate_model: bool                         Generate model or not
        :return: class                                      Object Model
        """
        if self.tcl_filename:
            m = ModelToTCL(self.analysis_type, self.sections_file, self.loads_file, self.materials_file,
                           self.outputsDir, self.system, hingeModel=self.hinge_model, flag3d=self.flag3d,
                           direction=self.direction, tcl_filename=self.tcl_filename)
        else:
            m = Model(self.analysis_type, self.sections_file, self.loads_file, self.materials_file,
                      self.outputsDir, self.system, hingeModel=self.hinge_model, flag3d=self.flag3d,
                      direction=self.direction)

        # Generate the model if specified
        if generate_model:
            m.model()

            if "PO" in self.analysis_type:
                m.define_loads(m.elements, apply_point=False)
                s = Static()
                if self.tcl_filename:
                    s.static_analysis(self.outputsDir, self.flag3d)
                else:
                    s.static_analysis(None, self.flag3d)

        return m

    def get_modal_parameters(self):
        try:
            if self.periods_ida is None:
                with open(self.modal_analysis_path) as f:
                    results = json.load(f)
                if self.flag3d:
                    if self.period_assignment:
                        period = [results["Periods"][self.period_assignment["x"]],
                                  results["Periods"][self.period_assignment["y"]]]
                    else:
                        period = [results["Periods"][0],
                                  results["Periods"][1]]
                else:
                    period = results["Periods"][0]

                damping = results["Damping"][0]
                omegas = results["CircFreq"]

            else:
                period = self.periods_ida
                damping = 0.05
                omegas = 2 * np.pi / (np.array(period))

        except:
            raise ValueError("[EXCEPTION] Modal analysis data does not exist.")

        return period, damping, omegas

    def run_model(self):
        """
        Initializes model creator and runs analysis
        :return: None
        """
        # Run analysis and record the goodies
        if self.analysis_type is None or -1 in self.analysis_type or not self.analysis_type:
            # Checks the integrity of the files for any sources of error
            self.analysis_type = []
            print("[CHECK] Checking the integrity of the model")
            m = self.call_model()
            m.perform_analysis()
            print("[SUCCESS] Integrity of model verified")

        elif "PO" in self.analysis_type or "pushover" in self.analysis_type:
            try:
                # Static pushover analysis needs to be run after modal analysis
                with open(self.modal_analysis_path) as f:
                    modal_analysis_outputs = json.load(f)

                # Modal shape as the SPO lateral load pattern shape
                if self.direction == 0:
                    tag = self.period_assignment["x"]
                    mode_shape = np.abs(modal_analysis_outputs[f"Mode{tag + 1}"])
                else:
                    tag = self.period_assignment["y"]
                    mode_shape = np.abs(modal_analysis_outputs[f"Mode{tag + 1}"])

                # Normalize, helps to avoid convergence issues
                mode_shape = mode_shape / max(mode_shape)
                mode_shape = np.round(mode_shape, 2)
                # Call and run the OpenSees model
                m = self.call_model()
                m.perform_analysis(spo_pattern=2, mode_shape=mode_shape)

            except:
                print("[WARNING] 1st Mode-proportional loading was selected. MA outputs are missing! "
                      "\nRunning with triangular pattern")
                m = self.call_model()
                m.perform_analysis(spo_pattern=1)

        elif "ELF" in self.analysis_type or "ELFM" in self.analysis_type and self.APPLY_GRAVITY_ELF:
            if self.flag3d:
                # It might still run though :)
                raise ValueError("[EXCEPTION] ELF for a 3D model not supported!")

            # Equivalent lateral force method of analysis
            m = self.call_model()
            m.perform_analysis()

        elif "TH" in self.analysis_type or "timehistory" in self.analysis_type or "IDA" in self.analysis_type:
            # Create a folder for NLTHA
            createFolder(self.outputsDir / "NLTHA")

            # Nonlinear time history analysis
            print("[INITIATE] IDA started")

            # Get MA parameters
            period, damping, omegas = self.get_modal_parameters()

            # Initialize
            ida = IDA_HTF_3D(self.FIRST_INT, self.INCR_STEP, self.max_runs, self.IM_type, period, damping, omegas,
                             self.analysis_time_step, self.drift_capacity, self.gmdir, self.gmfileNames,
                             self.analysis_type, self.sections_file, self.loads_file, self.materials_file, self.system,
                             hingeModel=self.hinge_model, pflag=True, flag3d=self.flag3d,
                             export_at_each_step=self.export_at_each_step)

            # The Set-up
            ida.establish_im(output_dir=self.outputsDir / "NLTHA")

            # Export results
            if not self.export_at_each_step:
                with open(self.outputsDir / "IDA.pickle", "wb") as handle:
                    pickle.dump(ida.outputs, handle)

            if os.path.exists(self.outputsDir / "IM.csv"):
                im_filename = "IM_temp.csv"
            else:
                im_filename = "IM.csv"
            np.savetxt(self.outputsDir / im_filename, ida.IM_output, delimiter=',')

            print("[SUCCESS] IDA done")

        elif "MSA" in self.analysis_type:
            # TODO, currently supports space and 3D with hysteretic modelling

            # Create a folder for NLTHA
            createFolder(self.outputsDir / "MSA")

            # Nonlinear time history analysis
            print("[INITIATE] MSA started")

            # The Set-up
            self.records = get_records(self.gmdir, self.outputsDir / "MSA", self.gmfileNames)

        else:
            # Runs static or modal analysis (ST or MA)
            m = self.call_model()
            m.define_loads(m.elements, apply_point=False)
            m.perform_analysis(damping=self.DAMPING)


if __name__ == "__main__":

    start_time = get_start_time()

    # Directories
    directory_x = Path.cwd().parents[0] / ".applications/LOSS Validation Manuscript/Case21/Cache/framex"
    materials_file = directory_x.parents[1] / "materials.csv"
    loads_file = directory_x.parents[1] / "action.csv"
    outputsDir = directory_x.parents[1] / "RCMRF"

    # X direction
    section_file_x = directory_x / "hinge_models.csv"

    # Y direction
    directory_y = Path.cwd().parents[0] / ".applications/LOSS Validation Manuscript/Case21/Cache/framey"
    section_file_y = directory_y / "hinge_models.csv"

    # Gravity elements
    directory_gr = Path.cwd().parents[0] / ".applications/LOSS Validation Manuscript/Case21/Cache"
    section_file_gr = directory_gr / "gravity_hinges.csv"

    # Directories
    section_file = {"x": section_file_x, "y": section_file_y, "gravity": section_file_gr}

    # GM directory
    gmdir = Path.cwd() / "sample/groundMotion"
    gmfileNames = ["GMR_names1.txt", "GMR_names2.txt", "GMR_dts.txt", "GMR_durs.txt"]

    # RCMRF inputs
    hingeModel = "Hysteretic"
    analysis_type = ["ST"]
    # Run MA always with direction = 0
    direction = 0
    flag3d = True
    export_at_each_step = True
    period_assignment = {"x": 0, "y": 1}
    periods = [0.72, 0.62]
    # Let's go...
    m = Main(section_file, loads_file, materials_file, outputsDir, gmdir=gmdir, gmfileNames=gmfileNames,
             analysis_type=analysis_type, system="Perimeter", hinge_model=hingeModel, flag3d=flag3d,
             direction=direction, export_at_each_step=export_at_each_step, period_assignment=period_assignment,
             periods_ida=periods, max_runs=15, tcl_filename="low")

    m.wipe()
    m.run_model()

    # Wipe the model
    m.wipe()

    # Time it
    get_time(start_time)
