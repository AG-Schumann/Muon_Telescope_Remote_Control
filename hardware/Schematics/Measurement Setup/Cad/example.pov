#version 3.7; // 3.6
global_settings { assumed_gamma 1.0 }
#default { finish { ambient 0.2 diffuse 0.9 } }
#default { pigment { rgb <0.678, 0.710, 0.741> } }

//------------------------------------------
#include "colors.inc"
#include "textures.inc"

//------------------------------------------
#include "example_textures.inc"
#include "example_meshes.inc"

//------------------------------------------
// Camera ----------------------------------
#declare CamUp = < 0, 0, 495.31>;
#declare CamRight = <880.55, 0, 0>;
#declare CamRotation = <-5.962184803204593, 0.2944630719532077, 160.2720467233772>;
#declare CamPosition = <112.97572326660156, 261.9858093261719, 43.1785888671875>;
camera {
	orthographic
	location <0, 0, 0>
	direction <0, 1, 0>
	up CamUp
	right CamRight
	rotate CamRotation
	translate CamPosition
}

// FreeCAD Light -------------------------------------
light_source { CamPosition color rgb <0.5, 0.5, 0.5> }

// Background ------------------------------

polygon {
	5, <-440.2735188802083, -247.6538543701172>, <-440.2735188802083, 247.6538543701172>, <440.2735188802083, 247.6538543701172>, <440.2735188802083, -247.6538543701172>, <-440.2735188802083, -247.6538543701172>
	pigment { color rgb<0.969, 0.969, 0.969> }
	finish { ambient 1 diffuse 0 }
	rotate <84.0378151967954, 0.2944630719532077, 160.2720467233772>
	translate <112.97572326660156, 261.9858093261719, 43.1785888671875>
	translate <-33522.60887622833, -93639.43338394165, -10387.06824183464>
}
sky_sphere {
	pigment {
		color rgb<0.969, 0.969, 0.969>
	}
}

//------------------------------------------

#include "example_user.inc"

// Objects in Scene ------------------------

//----- Assembly -----
//----- X_axis -----
//----- Y_axis -----
//----- Z_axis -----
//----- XY_plane -----
//----- XZ_plane -----
//----- YZ_plane -----
//----- Origin001 -----
//----- Scintillator_group -----
object { Scintillator_group_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Top_Part -----
object { Top_Part_mesh
		pigment { color rgb <0.800, 0.800, 0.902> }
finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Bottom_Part -----
object { Bottom_Part_mesh
		pigment { color rgb <0.800, 0.800, 0.902> }
finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Hinge_001 -----
object { Hinge_001_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Hinge_002 -----
object { Hinge_002_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Simulations -----
//----- Simulation001 -----
//----- Hinge_003 -----
object { Hinge_003_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Hinge_004 -----
object { Hinge_004_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Hinge_005 -----
object { Hinge_005_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- Hinge_006 -----
object { Hinge_006_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- PCBA:_Raspberry_Pi_4B -----
//----- Board_V001 -----
object { Board_V001_mesh
	finish {
	ambient rgb<0.333, 0.333, 0.333>
	emission rgb<0.000, 0.000, 0.000>
	phong 0.53 phong_size 44.999998807907104 
}

}

//----- TB6600 -----